//! Battery properties for both the Flipper One gauge and desktop batteries.
//!
//! The Flipper One gauge shares an I2C bus with the Type-C controller. Read its
//! sysfs once at startup, then use the snapshots in power_supply uevents.
//! Other gauges, including ACPI laptop batteries, can update sysfs without
//! broadcasting a change, so refresh their snapshot periodically.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use crate::netlink::Socket;

/// `NETLINK_KOBJECT_UEVENT`, and its one group: the kernel's own broadcast.
const KOBJECT_UEVENT: libc::c_int = 15;
const UEVENT_KERNEL: u32 = 1;

const SUPPLIES: &str = "/sys/class/power_supply";

/// A snapshot, by the property's sysfs name: `capacity`, `status`, `temp`, ...
pub type Props = HashMap<String, String>;

fn cell() -> &'static Mutex<Option<Arc<Props>>> {
    static LATEST: OnceLock<Mutex<Option<Arc<Props>>>> = OnceLock::new();
    LATEST.get_or_init(|| Mutex::new(None))
}

enum Source {
    Gauge,
    Polled(Mutex<Polled>),
}

#[derive(Default)]
struct Polled {
    checked: Option<Instant>,
    pack: Option<Arc<Props>>,
}

impl Polled {
    fn latest(&mut self, supplies: &Path, now: Instant) -> Option<Arc<Props>> {
        const INTERVAL: Duration = Duration::from_secs(2);
        if self.checked.is_none_or(|checked| now.duration_since(checked) >= INTERVAL) {
            self.pack = pack_dir(supplies).and_then(|dir| read_uevent_file(&dir)).map(Arc::new);
            self.checked = Some(now);
        }
        self.pack.clone()
    }
}

fn uses_uevents(dir: &Path) -> bool {
    dir.file_name().and_then(|name| name.to_str()).is_some_and(|name| {
        ["bq28z610", "bq28x610", "bq28z620"].iter().any(|prefix| name.starts_with(prefix))
    })
}

/// The latest battery snapshot, or None when no battery is available.
pub fn latest() -> Option<Arc<Props>> {
    static SOURCE: OnceLock<Source> = OnceLock::new();
    let supplies = Path::new(SUPPLIES);
    match SOURCE.get_or_init(|| {
        if let Some(dir) = pack_dir(supplies).filter(|dir| uses_uevents(dir)) {
            start(&dir);
            Source::Gauge
        } else {
            Source::Polled(Mutex::default())
        }
    }) {
        Source::Gauge => cell().lock().ok()?.clone(),
        Source::Polled(poll) => {
            let mut poll = poll.lock().ok()?;
            poll.latest(supplies, Instant::now())
        }
    }
}

/// One property of the last snapshot.
pub fn prop(name: &str) -> Option<String> {
    latest()?.get(name).cloned()
}

/// One property, as a number.
pub fn num(name: &str) -> Option<i64> {
    prop(name)?.parse().ok()
}

fn store(props: Option<Props>) {
    if let Ok(mut held) = cell().lock() {
        *held = props.map(Arc::new);
    }
}

fn start(dir: &Path) {
    // Subscribed before the gauge is read, so an announcement in between is not lost.
    let socket = Socket::subscribe(KOBJECT_UEVENT, UEVENT_KERNEL);
    store(read_uevent_file(dir));
    let socket = match socket {
        Ok(socket) => socket,
        Err(e) => {
            eprintln!("battery        no uevents, not following: {e}");
            return;
        }
    };
    let spawned = thread::Builder::new().name("watch-battery".into()).spawn(move || {
        let mut buf = vec![0u8; 8192];
        loop {
            let got = match socket.recv(&mut buf) {
                Ok(got) => got,
                // A burst overran the socket: what was dropped is older than what
                // comes next, so carry on.
                Err(e) if e.raw_os_error() == Some(libc::ENOBUFS) => continue,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => return,
            };
            match pack_event(&buf[..got]) {
                Some(Event::Changed(props)) => store(Some(props)),
                Some(Event::Removed) => store(None),
                None => {}
            }
        }
    });
    if let Err(e) = spawned {
        eprintln!("battery        no thread, not following: {e}");
    }
}

fn pack_dir(supplies: &Path) -> Option<PathBuf> {
    std::fs::read_dir(supplies)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|dir| {
            std::fs::read_to_string(dir.join("type")).is_ok_and(|t| t.trim() == "Battery")
        })
        .find(|dir| !std::fs::read_to_string(dir.join("scope")).is_ok_and(|s| s.trim() == "Device"))
}

fn read_uevent_file(dir: &Path) -> Option<Props> {
    let text = std::fs::read_to_string(dir.join("uevent")).ok()?;
    Some(props(text.lines().map(str::as_bytes)))
}

/// `POWER_SUPPLY_CAPACITY=87` and the like, by sysfs name: `capacity`.
fn props<'a>(fields: impl Iterator<Item = &'a [u8]>) -> Props {
    fields
        .filter_map(|field| std::str::from_utf8(field).ok())
        .filter_map(|field| field.strip_prefix("POWER_SUPPLY_"))
        .filter_map(|field| field.split_once('='))
        .map(|(key, value)| (key.to_ascii_lowercase(), value.to_string()))
        .collect()
}

#[derive(Debug, PartialEq)]
enum Event {
    Changed(Props),
    Removed,
}

/// What a uevent says about the pack, or None for anything else.
fn pack_event(message: &[u8]) -> Option<Event> {
    let fields = || message.split(|b| *b == 0);
    let has = |want: &[u8]| fields().any(|field| field == want);
    if !has(b"SUBSYSTEM=power_supply") || !has(b"POWER_SUPPLY_TYPE=Battery") {
        return None;
    }
    if has(b"ACTION=remove") {
        return Some(Event::Removed);
    }
    Some(Event::Changed(props(fields())))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(fields: &[&str]) -> Vec<u8> {
        fields.iter().flat_map(|f| f.bytes().chain([0])).collect()
    }

    #[test]
    fn a_pack_announcement_is_its_properties() {
        let message = event(&[
            "change@/devices/platform/i2c/bq28z620-0/power_supply/bq28z620-0",
            "ACTION=change",
            "SUBSYSTEM=power_supply",
            "POWER_SUPPLY_NAME=bq28z620-0",
            "POWER_SUPPLY_TYPE=Battery",
            "POWER_SUPPLY_STATUS=Discharging",
            "POWER_SUPPLY_CAPACITY=87",
            "POWER_SUPPLY_TEMP=312",
            "POWER_SUPPLY_POWER_AVG=-2412000",
        ]);
        let Some(Event::Changed(p)) = pack_event(&message) else { panic!() };
        assert_eq!(p["capacity"], "87");
        assert_eq!(p["status"], "Discharging");
        assert_eq!(p["temp"], "312");
        assert_eq!(p["power_avg"], "-2412000");
        assert!(!p.contains_key("action"));
    }

    #[test]
    fn the_charger_and_other_subsystems_are_not_the_pack() {
        let charger = event(&[
            "ACTION=change",
            "SUBSYSTEM=power_supply",
            "POWER_SUPPLY_TYPE=USB",
            "POWER_SUPPLY_ONLINE=1",
        ]);
        let typec = event(&["ACTION=change", "SUBSYSTEM=typec"]);
        assert_eq!(pack_event(&charger), None);
        assert_eq!(pack_event(&typec), None);
        let gone = event(&["ACTION=remove", "SUBSYSTEM=power_supply", "POWER_SUPPLY_TYPE=Battery"]);
        assert_eq!(pack_event(&gone), Some(Event::Removed));
    }

    #[test]
    fn laptop_percent_refreshes_without_a_uevent() {
        let supplies =
            std::env::temp_dir().join(format!("flipper-battery-poll-{}", std::process::id()));
        let dir = supplies.join("BAT0");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("type"), "Battery\n").unwrap();
        let write_capacity = |level| {
            std::fs::write(dir.join("uevent"), format!("POWER_SUPPLY_CAPACITY={level}\n")).unwrap();
        };
        let now = Instant::now();
        let mut poll = Polled::default();
        write_capacity(53);
        assert_eq!(poll.latest(&supplies, now).unwrap()["capacity"], "53");
        write_capacity(37);
        assert_eq!(poll.latest(&supplies, now + Duration::from_secs(1)).unwrap()["capacity"], "53");
        assert_eq!(poll.latest(&supplies, now + Duration::from_secs(2)).unwrap()["capacity"], "37");
        std::fs::remove_dir_all(&supplies).unwrap();
        assert!(poll.latest(&supplies, now + Duration::from_secs(4)).is_none());
        assert!(uses_uevents(Path::new("/sys/class/power_supply/bq28z620-0")));
        assert!(!uses_uevents(&dir));
    }

    #[test]
    fn the_uevent_file_reads_the_same_way() {
        let dir = std::env::temp_dir().join(format!("flipper-battery-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("uevent"),
            "POWER_SUPPLY_NAME=bq28z620-0\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_CAPACITY=5\n",
        )
        .unwrap();
        let p = read_uevent_file(&dir).unwrap();
        assert_eq!((p["capacity"].as_str(), p["type"].as_str()), ("5", "Battery"));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
