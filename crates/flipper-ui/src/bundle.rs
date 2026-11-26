//! Apps as AppImages in the user's `Apps` folder.
//!
//! A bundle is one file: the AppImage runtime with a squashfs behind it, and at the
//! root of that squashfs the `app.toml` flipctl reads. That file is what makes it
//! ours: a stock AppImage dropped into the same folder has none and is not listed.
//!
//! The manifest and the icon are read straight out of the squashfs, never by running
//! the file. The folder is where a person puts anything, and the scan runs inside a
//! unit that can reach the GPIO and USB, so a bundle is not executed to find out what
//! it is. What was read is kept beside a stamp of the file's size and mtime, and an
//! unchanged file's architecture is checked and its cached manifest reused.
//!
//! There are two folders, not one. The image ships its apps in a folder of its own,
//! which updates with the profile; the user's `Apps` is where anything they add
//! goes. `/home` is shared by every profile, so that one
//! survives a factory reset, which is the reason the apps moved there.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use crate::app::{self, AppEntry};
use crate::script;

/// The extension, matched without regard to case.
pub const EXT: &str = "AppImage";

/// Where the image puts the apps it ships: part of the profile root, so it updates
/// with a profile reinstall and survives a wiped home.
pub const SYSTEM: &str = "/usr/share/flipctl/apps";

/// The user's own folder, which is where anything dropped on the device lands.
pub fn root() -> PathBuf {
    app::home().join("Apps")
}

/// App directories in ascending order of precedence. A source launch adds the
/// checkout's apps before the user's directory, so a user's copy still wins.
///
/// A missing folder is not an error and not worth checking for: the walk of one that
/// is not there finds nothing, which is the same answer.
pub fn roots() -> Vec<PathBuf> {
    let mut roots = vec![PathBuf::from(SYSTEM)];
    if let Some(source) = source_root() {
        roots.extend([source.join("apps"), source.join("sample_apps")]);
    }
    roots.push(root());
    roots
}

fn source_root() -> Option<PathBuf> {
    std::env::var_os("FLIPCTL_SOURCE_ROOT").map(PathBuf::from).filter(|path| path.is_absolute())
}

/// Whether a file is one the image shipped rather than one the user added: the one
/// distinction that decides what may be deleted.
pub fn shipped(path: &Path) -> bool {
    path.starts_with(SYSTEM)
        || source_root().is_some_and(|source| {
            ["apps", "sample_apps", "target/desktop-apps"]
                .iter()
                .any(|dir| path.starts_with(source.join(dir)))
        })
}

/// Where a bundle's manifest and icon are kept once read, by key. Under the user's
/// cache, whichever root the bundle itself is in, so a shipped bundle costs the
/// profile nothing and the cache goes with the home.
pub fn cache_dir(key: &str) -> PathBuf {
    app::xdg_home("XDG_CACHE_HOME", ".cache").join("flipctl/bundles").join(key)
}

/// Why a file in the folder is not an app.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Skip {
    /// Not an AppImage at all, whatever it is called.
    NotAppImage,
    /// An ELF executable for a different CPU.
    WrongArchitecture,
    /// A file with no manifest of ours: a stock AppImage, or somebody's script.
    NotOurs,
    /// Could not be read; the text says why.
    Unreadable(String),
}

/// Apps under `root`, at any depth, sorted by folder then name.
///
/// A folder is a group, and its name is what the list shows on the way in. Hidden
/// entries are skipped and a symlinked directory is not followed, which is the only
/// way a walk of a directory tree loops.
///
/// Bundles are read first and the scripts after them, because a launcher is a bundle
/// and may declare a comment marker the scripts it runs are written behind.
///
/// The walk itself is plain file handling and stays out of the `bundle` feature, so a
/// build without a squashfs reader still finds the scripts.
pub fn discover(root: &Path) -> Vec<AppEntry> {
    discover_all(&[root.to_path_buf()])
}

/// The same, over several folders merged into one list.
///
/// An app is identified by its key, which is its path below the folder it was found
/// in, so the same app in two roots is one entry and the later root wins. That is
/// what lets somebody replace a shipped app with their own without touching what
/// the image put there.
///
/// The merge happens before anything is read, rather than by discovering each root
/// and concatenating: the comment markers scripts are recognised by come from the
/// runtime bundles, and a runtime the image ships has to be able to name a script
/// the user wrote.
pub fn discover_all(roots: &[PathBuf]) -> Vec<AppEntry> {
    let mut found: std::collections::BTreeMap<String, (PathBuf, Vec<String>)> =
        std::collections::BTreeMap::new();
    for root in roots {
        let mut here = Vec::new();
        walk(root, root, &[], &mut here);
        for (path, key, group) in here {
            if found.contains_key(&key) && is_bundle(&path) && foreign_elf(&path) {
                continue;
            }
            found.insert(key, (path, group));
        }
    }
    let files: Vec<(PathBuf, String, Vec<String>)> =
        found.into_iter().map(|(key, (path, group))| (path, key, group)).collect();

    let mut apps = Vec::new();
    for (path, key, group) in &files {
        if is_bundle(path) {
            if let Ok(app) = read_keyed(path, key, group) {
                apps.push(app);
            }
        }
    }
    if let Some(source) = source_root() {
        let local: Vec<_> = source_apps(&source)
            .filter(|entry| {
                entry.key != "source-doom" || !apps.iter().any(|app| app.name == entry.name)
            })
            .collect();
        apps.retain(|entry| {
            entry.provides.is_empty() || !local.iter().any(|app| app.provides == entry.provides)
        });
        apps.extend(local);
    }
    let markers = script::markers(&apps);
    for (path, key, group) in &files {
        if is_bundle(path) {
            continue;
        }
        if let Ok(app) = script::read(path, key, group, &markers) {
            apps.push(app);
        }
    }
    apps.sort_by(|a, b| (&a.group, &a.name).cmp(&(&b.group, &b.name)));
    apps
}

/// The desktop build keeps executable code and mutable app data separate, like
/// AppImages do. The original manifest still controls the name and capabilities.
fn source_apps(source: &Path) -> impl Iterator<Item = AppEntry> + '_ {
    fs::read_dir(source.join("apps")).into_iter().flatten().flatten().filter_map(move |entry| {
        let dir = entry.path();
        let name = entry.file_name().into_string().ok()?;
        let manifest = fs::read_to_string(dir.join(app::MANIFEST)).ok()?;
        let cmd = app::py_string(&manifest, "wayland")?;
        let binary = Path::new(&cmd).file_name()?;
        let launch = if binary == "AppRun" {
            let run = dir.join("AppRun");
            // These wrappers need only the assets in the checkout and system tools.
            let ready = match name.as_str() {
                "dither-test" | "video-player" => true,
                "doom" => cfg!(target_arch = "aarch64"),
                "python-runtime" => {
                    std::env::var_os("FLIPCTL_SOURCE_PYTHON").as_deref() == Some(run.as_os_str())
                }
                "js-runtime" => {
                    std::env::var_os("FLIPCTL_SOURCE_JS").as_deref() == Some(run.as_os_str())
                }
                _ => false,
            };
            if !ready {
                return None;
            }
            run
        } else {
            let artifacts = source.join("target/desktop-apps");
            [artifacts.join(std::env::consts::ARCH), artifacts.join("debug")]
                .into_iter()
                .map(|dir| dir.join(binary))
                .find(|path| path.is_file() && !foreign_elf(path))?
        };
        if !launch.is_file() {
            return None;
        }
        let group: Vec<_> = app::py_string(&manifest, "folder").into_iter().collect();
        let key = format!("source-{name}");
        let portable_doom = name == "doom" && cfg!(target_arch = "aarch64");
        let mut entry = app::parse_manifest(&manifest, name, &group, dir, launch, key)?;
        if portable_doom {
            entry.apt.extend(["dsda-doom".into(), "freedoom".into()]);
        }
        Some(entry)
    })
}

/// Every file under `root`, with what it is filed under and the folders it sits in.
fn walk(dir: &Path, root: &Path, group: &[String], out: &mut Vec<(PathBuf, String, Vec<String>)>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else { continue };
        let path = entry.path();
        let Some(name) = path.file_name().map(|n| n.to_string_lossy().to_string()) else {
            continue;
        };
        if name.starts_with('.') {
            continue;
        }
        if kind.is_dir() {
            let mut deeper = group.to_vec();
            deeper.push(name);
            walk(&path, root, &deeper, out);
        } else if kind.is_file() {
            let key = key(root, &path);
            out.push((path, key, group.to_vec()));
        }
    }
}

fn foreign_elf(path: &Path) -> bool {
    let native = match std::env::consts::ARCH {
        "x86_64" => 62,
        "aarch64" => 183,
        "arm" => 40,
        _ => return false,
    };
    let mut header = [0u8; 20];
    if fs::File::open(path).and_then(|mut file| file.read_exact(&mut header)).is_err()
        || !header.starts_with(b"\x7fELF")
    {
        return false;
    }
    let machine = match header[5] {
        1 => u16::from_le_bytes([header[18], header[19]]),
        2 => u16::from_be_bytes([header[18], header[19]]),
        _ => return false,
    };
    machine != 0 && machine != native
}

/// Whether this is a file the squashfs reader reads.
pub fn is_bundle(path: &Path) -> bool {
    path.extension().is_some_and(|e| e.to_string_lossy().eq_ignore_ascii_case(EXT))
}

/// What an app is filed under: its path below the folder with the separators turned
/// into dashes and the extension dropped, or its stem when it is elsewhere.
///
/// A bundle is named `<app>-<arch>.fap.AppImage`, which is two extensions deep, and
/// `file_stem` takes one off. The `.fap` says what the file is rather than what it
/// is called, so it comes off too and a work directory is not named after it.
pub fn key(root: &Path, path: &Path) -> String {
    let stem = path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let stem = stem.strip_suffix(".fap").unwrap_or(&stem).to_string();
    let Ok(rel) = path.strip_prefix(root) else {
        return stem;
    };
    let mut parts: Vec<String> = rel
        .parent()
        .into_iter()
        .flat_map(|p| p.components())
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect();
    parts.push(stem);
    parts.join("-")
}

#[cfg(feature = "bundle")]
mod imp {
    use std::fs::{self, File};
    use std::io::{BufReader, Read, Seek, SeekFrom};
    use std::path::{Path, PathBuf};
    use std::time::UNIX_EPOCH;

    use backhand::{FilesystemReader, InnerNode};

    use super::Skip;
    use crate::app::{self, AppEntry, MANIFEST};

    /// One bundle, wherever it sits: the hand-off from a desktop names any file.
    pub fn read(path: &Path, group: &[String]) -> Result<AppEntry, Skip> {
        read_keyed(path, &super::key(&super::root(), path), group)
    }

    /// Where what was read out of a bundle is kept.
    fn cache_dir(key: &str) -> PathBuf {
        super::cache_dir(key)
    }

    /// What a file turned out to be, kept beside what was read from it.
    #[derive(Debug, PartialEq, Eq, Clone, Copy)]
    enum Kind {
        App,
        Stock,
        Bogus,
    }

    /// The file a cache directory was made from, and what it turned out to be.
    ///
    /// The mtime to the nanosecond: a bundle replaced by another of the same size in
    /// the same second is still a different file.
    #[derive(Debug, PartialEq, Eq, Clone, Copy)]
    struct Stamp {
        size: u64,
        mtime: u128,
        kind: Kind,
    }

    impl Stamp {
        fn of(meta: &fs::Metadata, kind: Kind) -> Self {
            let mtime = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map_or(0, |d| d.as_nanos());
            Self { size: meta.len(), mtime, kind }
        }

        fn read(dir: &Path) -> Option<Self> {
            let text = fs::read_to_string(dir.join("stamp")).ok()?;
            let mut fields = text.split_whitespace();
            Some(Self {
                size: fields.next()?.parse().ok()?,
                mtime: fields.next()?.parse().ok()?,
                kind: match fields.next()? {
                    "app" => Kind::App,
                    "stock" => Kind::Stock,
                    "bogus" => Kind::Bogus,
                    _ => return None,
                },
            })
        }

        fn write(&self, dir: &Path) {
            let kind = match self.kind {
                Kind::App => "app",
                Kind::Stock => "stock",
                Kind::Bogus => "bogus",
            };
            let _ = fs::create_dir_all(dir);
            let _ = fs::write(dir.join("stamp"), format!("{} {} {kind}\n", self.size, self.mtime));
        }

        /// The same file, whatever it was found to be.
        fn same_file(&self, other: &Self) -> bool {
            self.size == other.size && self.mtime == other.mtime
        }
    }

    pub fn read_keyed(path: &Path, key: &str, group: &[String]) -> Result<AppEntry, Skip> {
        if super::foreign_elf(path) {
            return Err(Skip::WrongArchitecture);
        }
        let meta = fs::metadata(path).map_err(|e| Skip::Unreadable(e.to_string()))?;
        if !meta.is_file() {
            return Err(Skip::NotAppImage);
        }
        let dir = cache_dir(key);
        let now = Stamp::of(&meta, Kind::App);
        if let Some(have) = Stamp::read(&dir).filter(|have| have.same_file(&now)) {
            match have.kind {
                Kind::Stock => return Err(Skip::NotOurs),
                Kind::Bogus => return Err(Skip::NotAppImage),
                Kind::App => {
                    if let Some(mut cached) = fs::read_to_string(dir.join(MANIFEST))
                        .ok()
                        .and_then(|src| parse(&src, path, group, &dir, key))
                    {
                        // The icon the manifest names is only real if the copy is still
                        // there.
                        if !cached.icon.is_empty() && !dir.join(&cached.icon).is_file() {
                            cached.icon.clear();
                        }
                        return Ok(cached);
                    }
                }
            }
        }
        let opened = open(path, group, &dir, key);
        match &opened {
            Ok(app) => {
                Stamp::of(&meta, Kind::App).write(&dir);
                crate::logline!("bundles        {key}: {}", app.name);
            }
            Err(Skip::NotOurs) => {
                Stamp::of(&meta, Kind::Stock).write(&dir);
                crate::logline!("bundles        {key}: not a flipctl app, skipped");
            }
            Err(Skip::NotAppImage) => {
                Stamp::of(&meta, Kind::Bogus).write(&dir);
                crate::logline!("bundles        {key}: not an AppImage, skipped");
            }
            Err(Skip::WrongArchitecture) => {}
            Err(Skip::Unreadable(why)) => {
                crate::logline!("bundles        {key}: {why}");
            }
        }
        opened
    }

    fn parse(src: &str, path: &Path, group: &[String], dir: &Path, key: &str) -> Option<AppEntry> {
        let fallback =
            path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
        app::parse_manifest(
            src,
            fallback,
            group,
            dir.to_path_buf(),
            path.to_path_buf(),
            key.to_string(),
        )
    }

    /// Open the file, find the squashfs, and take the manifest and the icon out of it.
    fn open(path: &Path, group: &[String], dir: &Path, key: &str) -> Result<AppEntry, Skip> {
        let unreadable = |e: &dyn std::fmt::Display| Skip::Unreadable(e.to_string());
        let mut file = File::open(path).map_err(|e| unreadable(&e))?;
        let mut header = [0u8; 64];
        if file.read_exact(&mut header).is_err() || !is_type2(&header) {
            return Err(Skip::NotAppImage);
        }
        let offset = squashfs_offset(&header, &mut file).ok_or(Skip::NotAppImage)?;
        let squashfs = FilesystemReader::from_reader_with_offset(BufReader::new(file), offset)
            .map_err(|e| unreadable(&e))?;

        let manifest = root_file(&squashfs, MANIFEST).ok_or(Skip::NotOurs)?;
        let src = String::from_utf8_lossy(&manifest).into_owned();
        fs::create_dir_all(dir).map_err(|e| unreadable(&e))?;
        fs::write(dir.join(MANIFEST), &src).map_err(|e| unreadable(&e))?;
        let mut app = parse(&src, path, group, dir, key).ok_or(Skip::NotOurs)?;

        // The icon the manifest names, else the one the AppImage convention puts at
        // the root, which is usually a link to the real file.
        let wanted = if app.icon.is_empty() { dir_icon(&squashfs) } else { Some(app.icon.clone()) };
        app.icon.clear();
        if let Some(name) = wanted {
            if let Some(bytes) = root_file(&squashfs, &name) {
                if fs::write(dir.join(&name), bytes).is_ok() {
                    app.icon = name;
                }
            }
        }
        Ok(app)
    }

    /// ELF64, little-endian, with the type-2 AppImage magic at byte 8.
    fn is_type2(header: &[u8; 64]) -> bool {
        &header[..4] == b"\x7fELF"
            && header[4] == 2
            && header[5] == 1
            && &header[8..11] == b"AI\x02"
    }

    /// Where the squashfs starts: past the end of the ELF, which is the section header
    /// table or the last section, whichever reaches further. The runtime computes the
    /// same number, so this is the offset appimagetool wrote the image at.
    pub(super) fn squashfs_offset(header: &[u8; 64], file: &mut (impl Read + Seek)) -> Option<u64> {
        let shoff = u64::from_le_bytes(header[0x28..0x30].try_into().ok()?);
        let shentsize = u64::from(u16::from_le_bytes(header[0x3a..0x3c].try_into().ok()?));
        let shnum = u64::from(u16::from_le_bytes(header[0x3c..0x3e].try_into().ok()?));
        let mut end = shoff + shentsize * shnum;
        if shentsize >= 64 {
            for i in 0..shnum {
                file.seek(SeekFrom::Start(shoff + i * shentsize)).ok()?;
                let mut section = [0u8; 64];
                file.read_exact(&mut section).ok()?;
                // SHT_NOBITS occupies no bytes in the file.
                let kind = u32::from_le_bytes(section[4..8].try_into().ok()?);
                if kind == 8 {
                    continue;
                }
                let off = u64::from_le_bytes(section[0x18..0x20].try_into().ok()?);
                let size = u64::from_le_bytes(section[0x20..0x28].try_into().ok()?);
                end = end.max(off + size);
            }
        }
        (end > 0).then_some(end)
    }

    /// The bytes of a file at the root of the image.
    fn root_file(squashfs: &FilesystemReader, name: &str) -> Option<Vec<u8>> {
        let node = squashfs.files().find(|n| at_root(&n.fullpath, name))?;
        let InnerNode::File(file) = &node.inner else {
            return None;
        };
        let mut bytes = Vec::new();
        squashfs.file(file).reader().read_to_end(&mut bytes).ok()?;
        Some(bytes)
    }

    /// The file `.DirIcon` points at, or `.DirIcon` itself when it is a file.
    fn dir_icon(squashfs: &FilesystemReader) -> Option<String> {
        let node = squashfs.files().find(|n| at_root(&n.fullpath, ".DirIcon"))?;
        match &node.inner {
            InnerNode::Symlink(link) => {
                link.link.file_name().map(|n| n.to_string_lossy().to_string())
            }
            InnerNode::File(_) => Some(".DirIcon".to_string()),
            _ => None,
        }
    }

    fn at_root(full: &Path, name: &str) -> bool {
        full.file_name().is_some_and(|n| n == name)
            && full.parent().is_none_or(|p| p == Path::new("/") || p == Path::new(""))
    }

    /// Whether the runtime can mount a bundle, which needs the setuid `fusermount3`
    /// from the `fuse3` package. Asked once: a package does not appear mid-session.
    pub fn can_mount() -> bool {
        static FOUND: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        *FOUND.get_or_init(|| {
            let on_path = std::env::var_os("PATH").is_some_and(|path| {
                std::env::split_paths(&path).any(|dir| {
                    dir.join("fusermount3").is_file() || dir.join("fusermount").is_file()
                })
            });
            if !on_path {
                crate::logline!("bundles        no fusermount3, extracting instead");
            }
            on_path
        })
    }

    /// What a bundle launch needs in its environment beyond what every app gets.
    ///
    /// Without FUSE the runtime unpacks the image into a temporary directory and runs
    /// it from there, which is slow and visible rather than a failure.
    pub fn launch_env() -> Vec<String> {
        if can_mount() {
            Vec::new()
        } else {
            vec!["APPIMAGE_EXTRACT_AND_RUN=1".to_string()]
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::io::Cursor;

        /// Sections may sit past the section header table, and a NOBITS section
        /// occupies nothing, so the end is the furthest byte a real section reaches.
        #[test]
        fn the_squashfs_starts_where_the_elf_ends() {
            let mut elf = vec![0u8; 64 + 2 * 64];
            elf[..4].copy_from_slice(b"\x7fELF");
            elf[4] = 2;
            elf[5] = 1;
            elf[8..11].copy_from_slice(b"AI\x02");
            elf[0x28..0x30].copy_from_slice(&64u64.to_le_bytes());
            elf[0x3a..0x3c].copy_from_slice(&64u16.to_le_bytes());
            elf[0x3c..0x3e].copy_from_slice(&2u16.to_le_bytes());
            // Section 0: 100 bytes at 500. Section 1: NOBITS claiming 9000 at 1000.
            elf[64 + 0x18..64 + 0x20].copy_from_slice(&500u64.to_le_bytes());
            elf[64 + 0x20..64 + 0x28].copy_from_slice(&100u64.to_le_bytes());
            elf[128 + 4..128 + 8].copy_from_slice(&8u32.to_le_bytes());
            elf[128 + 0x18..128 + 0x20].copy_from_slice(&1000u64.to_le_bytes());
            elf[128 + 0x20..128 + 0x28].copy_from_slice(&9000u64.to_le_bytes());
            let header: [u8; 64] = elf[..64].try_into().unwrap();
            assert!(is_type2(&header));
            let mut cursor = Cursor::new(elf.clone());
            assert_eq!(squashfs_offset(&header, &mut cursor), Some(600));
            let mut plain = header;
            plain[8..11].copy_from_slice(b"\0\0\0");
            assert!(!is_type2(&plain));
        }

        #[test]
        fn a_stamp_round_trips() {
            let dir = std::env::temp_dir().join(format!("flipctl-stamp-{}", std::process::id()));
            let stamp = Stamp { size: 12, mtime: 34_000_000_001, kind: Kind::Stock };
            stamp.write(&dir);
            assert_eq!(Stamp::read(&dir), Some(stamp));
            assert!(stamp.same_file(&Stamp { kind: Kind::App, ..stamp }));
            let _ = fs::remove_dir_all(&dir);
        }
    }
}

#[cfg(not(feature = "bundle"))]
mod imp {
    use std::path::Path;

    use super::Skip;
    use crate::app::AppEntry;

    pub fn read(_path: &Path, _group: &[String]) -> Result<AppEntry, Skip> {
        Err(Skip::Unreadable("built without the bundle feature".into()))
    }

    pub fn read_keyed(_path: &Path, _key: &str, _group: &[String]) -> Result<AppEntry, Skip> {
        Err(Skip::Unreadable("built without the bundle feature".into()))
    }

    pub fn can_mount() -> bool {
        true
    }

    pub fn launch_env() -> Vec<String> {
        Vec::new()
    }
}

pub use imp::{can_mount, launch_env, read, read_keyed};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_the_path_below_the_folder() {
        let root = Path::new("/home/user/Apps");
        assert_eq!(key(root, &root.join("radio-aarch64.fap.AppImage")), "radio-aarch64");
        assert_eq!(key(root, &root.join("net/nmap.AppImage")), "net-nmap");
        assert_eq!(key(root, &root.join("stations.py")), "stations");
        assert_eq!(key(root, Path::new("/tmp/other.AppImage")), "other");
    }

    /// `.fap` marks a file as one of ours whatever it is written in, so a script
    /// carries it exactly as a bundle does and it is no part of the key either.
    #[test]
    fn the_marker_is_no_part_of_a_key() {
        let root = Path::new("/home/user/Apps");
        assert_eq!(key(root, &root.join("ping.fap.py")), "ping");
        assert_eq!(key(root, &root.join("uptime.fap.js")), "uptime");
        assert_eq!(
            key(root, &root.join("Test Tools/breathing-led.fap.py")),
            "Test Tools-breathing-led"
        );
        // Nothing here knows a language: the marker comes off whatever follows it, so
        // a runtime nobody has written yet needs no change to this.
        assert_eq!(key(root, &root.join("clock.fap.lua")), "clock");
        assert_eq!(key(root, &root.join("backup.fap.sh")), "backup");
        assert_eq!(key(root, &root.join("thing.fap.AppImage")), "thing");
        // A script that goes without it keys the same way it always did.
        assert_eq!(key(root, &root.join("Test Tools/eth-led.py")), "Test Tools-eth-led");
    }

    /// A folder holds both kinds, and only the bundles are read as squashfs. What a
    /// script is at all is the launchers' business, not this module's.
    #[test]
    fn a_folder_holds_both_kinds() {
        assert!(is_bundle(Path::new("/home/user/Apps/radio.AppImage")));
        assert!(!is_bundle(Path::new("/home/user/Apps/stations.py")));
        assert_eq!(crate::script::markers(&[]), ["#", "//", "--"]);
    }
}
