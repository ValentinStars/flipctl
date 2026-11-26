//! The panel as a Wayland surface, for when a compositor owns the display.
//!
//! The logical Flipper frame stays fixed. The compositor supplies the fullscreen
//! size, and this sink fits and centers the frame within that surface.

use std::collections::{HashSet, VecDeque};
use std::ffi::CString;
use std::io;
use std::os::fd::{AsFd, FromRawFd, OwnedFd};

use wayland_client::protocol::{
    wl_buffer, wl_compositor, wl_keyboard, wl_output, wl_pointer, wl_registry, wl_seat, wl_shm,
    wl_shm_pool, wl_surface, wl_touch,
};
use wayland_client::{delegate_noop, Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum};
use wayland_protocols::wp::cursor_shape::v1::client::{
    wp_cursor_shape_device_v1, wp_cursor_shape_manager_v1,
};
use wayland_protocols::xdg::shell::client::{xdg_surface, xdg_toplevel, xdg_wm_base};

use crate::pixel::{Gray8, Rect};
use crate::platform::{Frame, FrameSink, InputSource};
use crate::theme::{color, metric};
use crate::{FlipperKey, KeyEvent};

/// Two buffers, so a frame can be written while the compositor still holds the
/// other. One would mean waiting for release on every frame.
const BUFFERS: usize = 2;
const SOFT_KEY_LABELS: [&str; FlipperKey::SOFT_ROW.len()] = ["Z", "X", "C", "V", "B"];
// Generic pixel glyphs for Escape, View, Power, Edit and Run.
const SOFT_KEY_ICONS: [[u8; 7]; FlipperKey::SOFT_ROW.len()] = [
    [0b1000001, 0b0100010, 0b0010100, 0b0001000, 0b0010100, 0b0100010, 0b1000001],
    [0, 0b1111111, 0, 0b1111111, 0, 0b1111111, 0],
    [0b0001000, 0b0001000, 0b0101010, 0b1001001, 0b1000001, 0b0100010, 0b0011100],
    [0b1110111, 0b1000001, 0b1000001, 0, 0b1000001, 0b1000001, 0b1110111],
    [0, 0b0000001, 0b0000010, 0b1000100, 0b0101000, 0b0010000, 0],
];

pub struct WlSink {
    conn: Connection,
    queue: EventQueue<State>,
    state: State,
    surface: wl_surface::WlSurface,
    buffers: Vec<Buffer>,
    frame: Vec<Gray8>,
    pending: bool,
    drawn_keys: [Option<FlipperKey>; 2],
    w: u16,
    h: u16,
    scale: Option<u16>,
    amber: bool,
}

struct Buffer {
    buffer: wl_buffer::WlBuffer,
    map: *mut u8,
    len: usize,
    size: (u16, u16),
}

#[derive(Default)]
struct State {
    compositor: Option<wl_compositor::WlCompositor>,
    shm: Option<wl_shm::WlShm>,
    wm_base: Option<xdg_wm_base::XdgWmBase>,
    seat: Option<wl_seat::WlSeat>,
    outputs: Vec<(wl_output::WlOutput, Option<String>)>,
    configured: bool,
    next_size: Option<(u16, u16)>,
    size: Option<(u16, u16)>,
    layout: Option<Layout>,
    closed: bool,
    // One keyboard, however many times the seat announces its capabilities. sway
    // re-announces as input devices appear, and the Flipper brings three, so
    // binding per announcement gives two or three wl_keyboards and every press
    // arrives that many times: one Down moves the selection twice, or acts on the
    // screen just left as well as the one arrived at.
    keyboard: Option<wl_keyboard::WlKeyboard>,
    pointer: Option<wl_pointer::WlPointer>,
    cursor_shapes: Option<wp_cursor_shape_manager_v1::WpCursorShapeManagerV1>,
    cursor_surface: Option<wl_surface::WlSurface>,
    cursor_theme: Option<wayland_cursor::CursorTheme>,
    pointer_pos: (f64, f64),
    active_ptr_key: Option<FlipperKey>,
    app_pointer: bool,
    pointer_in_app: bool,
    touch: Option<wl_touch::WlTouch>,
    active_touch: Option<(i32, FlipperKey)>,
    touch_in_app: Option<i32>,
    app_events: VecDeque<AppPointer>,
    busy: HashSet<wayland_client::backend::ObjectId>,
    keys: VecDeque<KeyEvent>,
}

/// Mouse and single-finger touchscreen input in logical panel coordinates.
#[derive(Clone, Copy)]
pub enum AppPointer {
    Motion(u32, u32),
    Press(u32, u32),
    Release,
}

#[derive(Clone, Copy)]
struct Layout {
    origin: (u16, u16),
    content: (u16, u16),
    controls_height: u16,
}

impl Layout {
    fn new(size: (u16, u16), source: (u16, u16), limit: Option<u16>) -> Self {
        let (w, h) = size;
        let (sw, sh) = source;
        let scale = (w / sw).min(h / sh).min(limit.unwrap_or(u16::MAX));
        let (cw, ch) = if scale > 0 {
            (sw * scale, sh * scale)
        } else if u32::from(w) * u32::from(sh) <= u32::from(h) * u32::from(sw) {
            (w, (u32::from(w) * u32::from(sh) / u32::from(sw)).max(1) as u16)
        } else {
            ((u32::from(h) * u32::from(sw) / u32::from(sh)).max(1) as u16, h)
        };
        let spare_height = h - ch;
        let controls_height =
            if spare_height >= 32 && cw >= crate::PANEL_W { spare_height.min(64) } else { 0 };
        Self {
            origin: ((w - cw) / 2, (spare_height - controls_height) / 2),
            content: (cw, ch),
            controls_height,
        }
    }

    fn controls(self) -> impl Iterator<Item = (Rect, FlipperKey, &'static str)> {
        let (left, top) = self.origin;
        let (width, height) = self.content;
        let scale_x =
            move |x: i32| (u32::from(width) * x as u32 / u32::from(crate::PANEL_W)) as u16;
        const VERTICAL_INSET: u16 = 6;
        FlipperKey::SOFT_ROW
            .into_iter()
            .zip(SOFT_KEY_LABELS)
            .enumerate()
            .filter(move |_| self.controls_height != 0)
            .map(move |(index, (key, label))| {
                let slot = crate::layout::soft_slot_x(index);
                let start = scale_x(slot);
                let end = scale_x(slot + metric::BUTTON_W);
                let rect = Rect::new(
                    left + start,
                    top + height + VERTICAL_INSET,
                    end - start,
                    self.controls_height - VERTICAL_INSET * 2,
                );
                (rect, key, label)
            })
    }

    fn key_at(self, x: f64, y: f64) -> Option<FlipperKey> {
        for (rect, key, _) in self.controls() {
            if (f64::from(rect.x)..f64::from(rect.x + rect.w)).contains(&x)
                && (f64::from(rect.y)..f64::from(rect.y + rect.h)).contains(&y)
            {
                return Some(key);
            }
        }

        let (x, y) = self.panel_point(x, y)?;
        Some(if y >= 124 {
            let slot = (x / (u32::from(crate::PANEL_W) / 5)).min(4);
            FlipperKey::SOFT_ROW[slot as usize]
        } else if y < 22 {
            FlipperKey::Back
        } else if self.controls_height != 0 {
            return None;
        } else if x < 48 {
            FlipperKey::Left
        } else if x > 208 {
            FlipperKey::Right
        } else if y < 65 {
            FlipperKey::Up
        } else if y > 90 {
            FlipperKey::Down
        } else {
            FlipperKey::Ok
        })
    }

    fn panel_point(self, x: f64, y: f64) -> Option<(u32, u32)> {
        let x = x - f64::from(self.origin.0);
        let y = y - f64::from(self.origin.1);
        let (cw, ch) = self.content;
        if !(0.0..f64::from(cw)).contains(&x) || !(0.0..f64::from(ch)).contains(&y) {
            return None;
        }
        Some((
            (x * f64::from(crate::PANEL_W) / f64::from(cw)) as u32,
            (y * f64::from(crate::PANEL_H) / f64::from(ch)) as u32,
        ))
    }
}

impl State {
    fn app_point(&self, x: f64, y: f64) -> Option<(u32, u32)> {
        if !self.app_pointer {
            return None;
        }
        let layout = self.layout?;
        layout.panel_point(x, y).filter(|&(_, y)| {
            y >= metric::STATUS_BAR_H as u32 && (layout.controls_height != 0 || y < 124)
        })
    }

    fn app_motion(&mut self, x: f64, y: f64) {
        if let Some((x, y)) = self.app_point(x, y) {
            if matches!(self.app_events.back(), Some(AppPointer::Motion(..))) {
                self.app_events.pop_back();
            }
            self.app_events.push_back(AppPointer::Motion(x, y));
        }
    }

    fn show_cursor(
        &mut self,
        pointer: &wl_pointer::WlPointer,
        serial: u32,
        qh: &QueueHandle<Self>,
    ) {
        if let Some(manager) = &self.cursor_shapes {
            let device = manager.get_pointer(pointer, qh, ());
            device.set_shape(serial, wp_cursor_shape_device_v1::Shape::Default);
            device.destroy();
            return;
        }
        let Some(surface) = &self.cursor_surface else { return };
        let Some(cursor) =
            self.cursor_theme.as_mut().and_then(|theme| theme.get_cursor("left_ptr"))
        else {
            return;
        };
        let image = &cursor[0];
        let (x, y) = image.hotspot();
        let (width, height) = image.dimensions();
        pointer.set_cursor(serial, Some(surface), x as i32, y as i32);
        surface.attach(Some(image), 0, 0);
        surface.damage(0, 0, width as i32, height as i32);
        surface.commit();
    }

    fn release_pointer(&mut self) {
        if std::mem::take(&mut self.pointer_in_app) {
            self.app_events.push_back(AppPointer::Release);
        }
        if let Some(key) = self.active_ptr_key.take() {
            self.keys.push_back(KeyEvent { key, down: false });
        }
    }

    fn release_touch(&mut self) {
        if self.touch_in_app.take().is_some() {
            self.app_events.push_back(AppPointer::Release);
        }
        if let Some((_, key)) = self.active_touch.take() {
            self.keys.push_back(KeyEvent { key, down: false });
        }
    }
}

impl WlSink {
    /// Open on the compositor's default output.
    pub fn new(w: u16, h: u16) -> io::Result<Self> {
        Self::on_output(w, h, None)
    }

    /// Present a logical frame of `w` by `h` on the named Wayland output.
    pub fn on_output(w: u16, h: u16, output: Option<&str>) -> io::Result<Self> {
        if w == 0 || h == 0 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "empty Wayland frame"));
        }
        let conn = Connection::connect_to_env().map_err(io::Error::other)?;
        let (globals, mut queue) = wayland_client::globals::registry_queue_init::<State>(&conn)
            .map_err(io::Error::other)?;
        let qh = queue.handle();

        let mut state = State::default();
        for g in globals.contents().clone_list() {
            match g.interface.as_str() {
                "wl_compositor" => {
                    state.compositor =
                        Some(globals.registry().bind(g.name, 4.min(g.version), &qh, ()))
                }
                "wl_shm" => state.shm = Some(globals.registry().bind(g.name, 1, &qh, ())),
                "xdg_wm_base" => {
                    state.wm_base = Some(globals.registry().bind(g.name, 1.min(g.version), &qh, ()))
                }
                "wp_cursor_shape_manager_v1" => {
                    state.cursor_shapes = Some(globals.registry().bind(g.name, 1, &qh, ()))
                }
                "wl_seat" => {
                    state.seat = Some(globals.registry().bind(g.name, 5.min(g.version), &qh, ()))
                }
                "wl_output" if output.is_some() && g.version >= 4 => {
                    state.outputs.push((globals.registry().bind(g.name, 4, &qh, ()), None));
                }
                _ => {}
            }
        }

        if let (None, Some(compositor), Some(shm)) =
            (&state.cursor_shapes, &state.compositor, &state.shm)
        {
            state.cursor_surface = Some(compositor.create_surface(&qh, ()));
            state.cursor_theme = wayland_cursor::CursorTheme::load(&conn, shm.clone(), 24).ok();
        }

        let target = if let Some(name) = output {
            queue.roundtrip(&mut state).map_err(io::Error::other)?;
            Some(
                state
                    .outputs
                    .iter()
                    .find(|(_, candidate)| candidate.as_deref() == Some(name))
                    .map(|(proxy, _)| proxy.clone())
                    .ok_or_else(|| {
                        let names = state
                            .outputs
                            .iter()
                            .filter_map(|(_, name)| name.as_deref())
                            .collect::<Vec<_>>()
                            .join(", ");
                        io::Error::new(
                            io::ErrorKind::NotFound,
                            format!("Wayland output {name:?} unavailable (named outputs: {names})"),
                        )
                    })?,
            )
        } else {
            None
        };

        let missing = |what: &str| io::Error::other(format!("compositor has no {what}"));
        let compositor = state.compositor.clone().ok_or_else(|| missing("wl_compositor"))?;
        state.shm.as_ref().ok_or_else(|| missing("wl_shm"))?;
        let wm_base = state.wm_base.clone().ok_or_else(|| missing("xdg_wm_base"))?;

        let surface = compositor.create_surface(&qh, ());
        let xdg = wm_base.get_xdg_surface(&surface, &qh, ());
        let toplevel = xdg.get_toplevel(&qh, ());
        toplevel.set_app_id("net.flipper.flipctl".into());
        toplevel.set_title("FlipCTL".into());

        let amber = std::env::var("FLIPCTL_AMBER")
            .map(|v| v != "0" && v != "false" && v != "no")
            .unwrap_or(true);
        let scale = std::env::var("FLIPCTL_SCALE")
            .ok()
            .and_then(|value| value.parse::<u16>().ok())
            .filter(|&value| value > 0)
            .map(|value| value.min(8));

        toplevel.set_fullscreen(target.as_ref());
        surface.commit();

        let mut sink = Self {
            conn,
            queue,
            state,
            surface,
            buffers: Vec::new(),
            frame: Vec::new(),
            pending: false,
            drawn_keys: [None; 2],
            w,
            h,
            scale,
            amber,
        };
        for _ in 0..100 {
            sink.pump()?;
            if sink.state.closed {
                return Err(io::Error::other("Wayland surface closed during setup"));
            }
            if sink.state.configured {
                return Ok(sink);
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        Err(io::Error::new(io::ErrorKind::TimedOut, "waiting for Wayland surface configure"))
    }

    pub fn set_app_pointer(&mut self, enabled: bool) {
        if self.state.app_pointer != enabled {
            self.state.app_pointer = enabled;
            self.state.pointer_in_app = false;
            self.state.touch_in_app = None;
            self.state.app_events.clear();
        }
    }

    pub fn poll_app_pointer(&mut self) -> Option<AppPointer> {
        self.state.app_events.pop_front()
    }

    /// Read whatever the compositor has said, without blocking.
    pub fn pump(&mut self) -> io::Result<()> {
        self.conn.flush().map_err(io::Error::other)?;
        self.queue.dispatch_pending(&mut self.state).map_err(io::Error::other)?;
        if let Some(guard) = self.conn.prepare_read() {
            match guard.read() {
                Ok(_) => {}
                Err(wayland_client::backend::WaylandError::Io(e))
                    if e.kind() == io::ErrorKind::WouldBlock => {}
                Err(e) => return Err(io::Error::other(e)),
            }
            self.queue.dispatch_pending(&mut self.state).map_err(io::Error::other)?;
        }
        self.present()
    }

    /// True once the compositor or the user has asked us to go away.
    pub fn closed(&self) -> bool {
        self.state.closed
    }

    fn present(&mut self) -> io::Result<()> {
        if self.frame.is_empty() || !self.state.configured || self.state.closed {
            return Ok(());
        }

        let fallback_scale = self.scale.unwrap_or(2);
        let size = self.state.size.unwrap_or((
            self.w.saturating_mul(fallback_scale),
            self.h.saturating_mul(fallback_scale),
        ));
        let layout = Layout::new(size, (self.w, self.h), self.scale);
        let resized = self.buffers.first().is_none_or(|buffer| buffer.size != size);
        if resized {
            let shm = self.state.shm.as_ref().expect("wl_shm was checked on connect");
            let queue = self.queue.handle();
            let buffers = (0..BUFFERS)
                .map(|index| Buffer::new(shm, &queue, size.0, size.1, index))
                .collect::<io::Result<Vec<_>>>()?;
            self.state.busy.clear();
            self.buffers = buffers;
            let (width, height) = size;
            let (content_width, content_height) = layout.content;
            eprintln!("Wayland surface {width}x{height}, frame {content_width}x{content_height}");
        }

        let active_keys = [self.state.active_ptr_key, self.state.active_touch.map(|(_, key)| key)];
        if !self.pending && !resized && active_keys == self.drawn_keys {
            return Ok(());
        }
        let Some(buf) =
            self.buffers.iter().find(|buffer| !self.state.busy.contains(&buffer.buffer.id()))
        else {
            return Ok(());
        };
        let dst = unsafe { std::slice::from_raw_parts_mut(buf.map, buf.len) };
        let stride = usize::from(size.0) * 4;
        let (left, top) = layout.origin;
        let (cw, ch) = layout.content;
        let (sw, sh) = (usize::from(self.w), usize::from(self.h));
        let (cw, ch) = (usize::from(cw), usize::from(ch));
        let x_indices: Vec<_> = (0..cw).map(|x| x * sw / cw).collect();
        let palette = if self.amber { &AMBER_PALETTE } else { &GRAY_PALETTE };
        let row_width = cw * 4;
        let origin = usize::from(top) * stride + usize::from(left) * 4;
        for y in 0..ch {
            let start = origin + y * stride;
            let row_index = y * sh / ch;
            if y > 0 && row_index == (y - 1) * sh / ch {
                dst.copy_within(start - stride..start - stride + row_width, start);
                continue;
            }
            let row = &self.frame[row_index * sw..][..sw];
            for (pixel, &x) in dst[start..start + row_width].chunks_exact_mut(4).zip(&x_indices) {
                pixel.copy_from_slice(&palette[usize::from(row[x].0)]);
            }
        }

        for ((rect, key, label), icon) in layout.controls().zip(SOFT_KEY_ICONS) {
            draw_control(
                dst,
                stride,
                rect,
                label,
                icon,
                active_keys.contains(&Some(key)),
                self.amber,
            );
        }

        self.drawn_keys = active_keys;
        self.state.layout = Some(layout);
        self.state.busy.insert(buf.buffer.id());
        self.surface.attach(Some(&buf.buffer), 0, 0);
        let (width, height) = (i32::from(size.0), i32::from(size.1));
        if self.surface.version() >= 4 {
            self.surface.damage_buffer(0, 0, width, height);
        } else {
            self.surface.damage(0, 0, width, height);
        }
        self.surface.commit();
        self.pending = false;
        self.conn.flush().map_err(io::Error::other)?;
        Ok(())
    }
}

const PALETTE_LEN: usize = 1 << u8::BITS;
const GRAY_PALETTE: [[u8; 4]; PALETTE_LEN] = palette(false);
const AMBER_PALETTE: [[u8; 4]; PALETTE_LEN] = palette(true);

const fn palette(amber: bool) -> [[u8; 4]; PALETTE_LEN] {
    let mut colors = [[0; 4]; PALETTE_LEN];
    let mut index = 0;
    while index < colors.len() {
        colors[index] = pixel_color(index as u8, amber);
        index += 1;
    }
    colors
}

const fn pixel_color(v: u8, amber: bool) -> [u8; 4] {
    let (b, g) = if amber { (0, (v as u16 * 130 / 255) as u8) } else { (v, v) };
    [b, g, v, 0xff]
}

fn draw_control(
    dst: &mut [u8],
    stride: usize,
    rect: Rect,
    label: &str,
    icon: [u8; 7],
    pressed: bool,
    amber: bool,
) {
    let mut plot = |x: u16, y: u16, light| {
        let index = usize::from(rect.y + y) * stride + usize::from(rect.x + x) * 4;
        dst[index..index + 4].copy_from_slice(&pixel_color(light, amber));
    };
    let edge = color::WHITE.0;
    let (fill, ink) = if pressed { (color::BLACK.0, edge) } else { (edge, color::BLACK.0) };
    for y in 0..rect.h {
        let inset = (metric::BUTTON_CORNER as u16 - 1).saturating_sub(y.min(rect.h - 1 - y));
        for x in inset..rect.w - inset {
            let is_edge = y == 0 || y == rect.h - 1 || x == inset || x == rect.w - inset - 1;
            plot(x, y, if is_edge { edge } else { fill });
        }
    }

    let font = &crate::font::TITLE;
    let scale = if rect.h >= 32 { 2 } else { 1 };
    let start = (rect.w - (7 + 4 + font.text_width(label)) * scale) / 2;
    let icon_y = (rect.h - 7 * scale) / 2;
    let x = start + (7 + 4) * scale;
    let y = (rect.h - u16::from(font.rows) * scale) / 2;
    let mut glyph_pixel = |x, y| {
        for dy in 0..scale {
            for dx in 0..scale {
                plot(x + dx, y + dy, ink);
            }
        }
    };
    for (row, bits) in icon.into_iter().enumerate() {
        for col in 0..7 {
            if bits & (1 << (6 - col)) != 0 {
                glyph_pixel(start + col * scale, icon_y + row as u16 * scale);
            }
        }
    }
    font.for_each_pixel(label, 0, 0, |px, py| {
        glyph_pixel(x + px as u16 * scale, y + py as u16 * scale);
    });
}

impl FrameSink for WlSink {
    fn commit(&mut self, frame: Frame<'_>, _damage: Rect) -> io::Result<()> {
        if (frame.w, frame.h) != (self.w, self.h)
            || frame.pixels.len() != usize::from(self.w) * usize::from(self.h)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid Wayland frame dimensions",
            ));
        }
        self.frame.clear();
        self.frame.extend_from_slice(frame.pixels);
        self.pending = true;
        self.pump()
    }
}

impl InputSource for WlSink {
    fn poll(&mut self) -> Option<KeyEvent> {
        if self.state.keys.is_empty() {
            let _ = self.pump();
        }
        self.state.keys.pop_front()
    }
}

impl Buffer {
    fn new(
        shm: &wl_shm::WlShm,
        qh: &QueueHandle<State>,
        w: u16,
        h: u16,
        index: usize,
    ) -> io::Result<Self> {
        let stride = usize::from(w) * 4;
        let pool_size =
            stride.checked_mul(usize::from(h)).and_then(|len| i32::try_from(len).ok()).ok_or_else(
                || io::Error::new(io::ErrorKind::InvalidInput, "Wayland buffer too large"),
            )?;
        let len = pool_size as usize;
        let name = CString::new(format!("flipctl-frame-{index}")).map_err(io::Error::other)?;
        let raw = unsafe { libc::memfd_create(name.as_ptr(), 0) };
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        let fd = unsafe { OwnedFd::from_raw_fd(raw) };
        if unsafe { libc::ftruncate(raw, len as libc::off_t) } < 0 {
            return Err(io::Error::last_os_error());
        }
        let map = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                raw,
                0,
            )
        };
        if map == libc::MAP_FAILED {
            return Err(io::Error::last_os_error());
        }
        let pool = shm.create_pool(fd.as_fd(), pool_size, qh, ());
        let buffer = pool.create_buffer(
            0,
            i32::from(w),
            i32::from(h),
            stride as i32,
            wl_shm::Format::Xrgb8888,
            qh,
            (),
        );
        pool.destroy();
        Ok(Self { buffer, map: map.cast(), len, size: (w, h) })
    }
}

impl Drop for Buffer {
    fn drop(&mut self) {
        self.buffer.destroy();
        unsafe { libc::munmap(self.map.cast(), self.len) };
    }
}

impl Dispatch<xdg_wm_base::XdgWmBase, ()> for State {
    fn event(
        _: &mut Self,
        base: &xdg_wm_base::XdgWmBase,
        event: xdg_wm_base::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        // Answering the ping is not optional: a compositor is entitled to consider
        // a client that does not respond unresponsive, and kill it.
        if let xdg_wm_base::Event::Ping { serial } = event {
            base.pong(serial);
        }
    }
}

impl Dispatch<xdg_surface::XdgSurface, ()> for State {
    fn event(
        state: &mut Self,
        surface: &xdg_surface::XdgSurface,
        event: xdg_surface::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let xdg_surface::Event::Configure { serial } = event {
            surface.ack_configure(serial);
            state.size = state.next_size;
            state.configured = true;
        }
    }
}

impl Dispatch<xdg_toplevel::XdgToplevel, ()> for State {
    fn event(
        state: &mut Self,
        _: &xdg_toplevel::XdgToplevel,
        event: xdg_toplevel::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            xdg_toplevel::Event::Close => state.closed = true,
            xdg_toplevel::Event::Configure { width, height, .. } if width > 0 && height > 0 => {
                state.next_size = u16::try_from(width).ok().zip(u16::try_from(height).ok());
            }
            _ => {}
        }
    }
}

impl Dispatch<wl_buffer::WlBuffer, ()> for State {
    fn event(
        state: &mut Self,
        buffer: &wl_buffer::WlBuffer,
        event: wl_buffer::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_buffer::Event::Release = event {
            state.busy.remove(&buffer.id());
        }
    }
}

impl Dispatch<wl_output::WlOutput, ()> for State {
    fn event(
        state: &mut Self,
        output: &wl_output::WlOutput,
        event: wl_output::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Name { name } = event {
            if let Some((_, stored)) = state.outputs.iter_mut().find(|(proxy, _)| proxy == output) {
                *stored = Some(name);
            }
        }
    }
}

impl Dispatch<wl_seat::WlSeat, ()> for State {
    fn event(
        state: &mut Self,
        seat: &wl_seat::WlSeat,
        event: wl_seat::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if let wl_seat::Event::Capabilities { capabilities: WEnum::Value(caps) } = event {
            let has_kb = caps.contains(wl_seat::Capability::Keyboard);
            match (has_kb, state.keyboard.take()) {
                (true, None) => state.keyboard = Some(seat.get_keyboard(qh, ())),
                (true, Some(k)) => state.keyboard = Some(k),
                (false, Some(k)) if k.version() >= 3 => k.release(),
                (false, Some(_)) => {}
                (false, None) => {}
            }
            let has_ptr = caps.contains(wl_seat::Capability::Pointer);
            match (has_ptr, state.pointer.take()) {
                (true, None) => state.pointer = Some(seat.get_pointer(qh, ())),
                (true, Some(p)) => state.pointer = Some(p),
                (false, Some(p)) => {
                    state.release_pointer();
                    if p.version() >= 3 {
                        p.release();
                    }
                }
                (false, None) => {}
            }
            match (caps.contains(wl_seat::Capability::Touch), state.touch.take()) {
                (true, None) => state.touch = Some(seat.get_touch(qh, ())),
                (true, Some(touch)) => state.touch = Some(touch),
                (false, Some(touch)) => {
                    state.release_touch();
                    if touch.version() >= 3 {
                        touch.release();
                    }
                }
                (false, None) => {}
            }
        }
    }
}

impl Dispatch<wl_pointer::WlPointer, ()> for State {
    fn event(
        state: &mut Self,
        pointer: &wl_pointer::WlPointer,
        event: wl_pointer::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_pointer::Event::Enter { serial, surface_x, surface_y, .. } => {
                state.pointer_pos = (surface_x, surface_y);
                state.show_cursor(pointer, serial, qh);
            }
            wl_pointer::Event::Motion { surface_x, surface_y, .. } => {
                state.pointer_pos = (surface_x, surface_y);
                if state.active_ptr_key.is_none() && state.touch_in_app.is_none() {
                    state.app_motion(surface_x, surface_y);
                }
            }
            wl_pointer::Event::Button { button: 0x110, state: s, .. } => {
                if matches!(s, WEnum::Value(wl_pointer::ButtonState::Pressed)) {
                    if let Some((x, y)) = state.app_point(state.pointer_pos.0, state.pointer_pos.1)
                    {
                        if state.touch_in_app.is_none() {
                            state.pointer_in_app = true;
                            state.app_events.push_back(AppPointer::Press(x, y));
                        }
                    } else if let Some(key) = state
                        .layout
                        .and_then(|layout| layout.key_at(state.pointer_pos.0, state.pointer_pos.1))
                    {
                        state.active_ptr_key = Some(key);
                        state.keys.push_back(KeyEvent { key, down: true });
                    }
                } else {
                    state.release_pointer();
                }
            }
            wl_pointer::Event::Leave { .. } => {
                state.release_pointer();
            }
            _ => {}
        }
    }
}

impl Dispatch<wl_touch::WlTouch, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_touch::WlTouch,
        event: wl_touch::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            wl_touch::Event::Down { id, x, y, .. }
                if state.active_touch.is_none() && state.touch_in_app.is_none() =>
            {
                if let Some((x, y)) = state.app_point(x, y) {
                    if !state.pointer_in_app {
                        state.touch_in_app = Some(id);
                        state.app_events.push_back(AppPointer::Press(x, y));
                    }
                } else if let Some(key) = state.layout.and_then(|layout| layout.key_at(x, y)) {
                    state.active_touch = Some((id, key));
                    state.keys.push_back(KeyEvent { key, down: true });
                }
            }
            wl_touch::Event::Motion { id, x, y, .. } if state.touch_in_app == Some(id) => {
                state.app_motion(x, y);
            }
            wl_touch::Event::Up { id, .. }
                if state.touch_in_app == Some(id)
                    || state.active_touch.is_some_and(|(active, _)| active == id) =>
            {
                state.release_touch();
            }
            wl_touch::Event::Cancel => state.release_touch(),
            _ => {}
        }
    }
}

impl Dispatch<wl_keyboard::WlKeyboard, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_keyboard::WlKeyboard,
        event: wl_keyboard::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        // Wayland carries evdev keycodes, which is the same space the panel's own
        // input driver uses, so the existing mapping applies unchanged.
        if let wl_keyboard::Event::Key { key, state: s, .. } = event {
            if let Ok(code) = u16::try_from(key) {
                if let Some(k) = FlipperKey::from_evdev(code) {
                    let down = matches!(s, WEnum::Value(wl_keyboard::KeyState::Pressed));
                    state.keys.push_back(KeyEvent { key: k, down });
                }
            }
        }
    }
}

impl Dispatch<wl_registry::WlRegistry, wayland_client::globals::GlobalListContents> for State {
    fn event(
        _: &mut Self,
        _: &wl_registry::WlRegistry,
        _: wl_registry::Event,
        _: &wayland_client::globals::GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

delegate_noop!(State: ignore wl_compositor::WlCompositor);
delegate_noop!(State: ignore wl_shm::WlShm);
delegate_noop!(State: ignore wl_shm_pool::WlShmPool);
delegate_noop!(State: ignore wl_surface::WlSurface);
delegate_noop!(State: ignore wp_cursor_shape_manager_v1::WpCursorShapeManagerV1);
delegate_noop!(State: ignore wp_cursor_shape_device_v1::WpCursorShapeDeviceV1);

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{PANEL_H, PANEL_W};

    const PANEL: (u16, u16) = (PANEL_W, PANEL_H);

    #[test]
    fn widescreen_uses_integer_scale_and_input_tracks_margins() {
        let layout = Layout::new((800, 480), PANEL, None);
        assert_eq!(layout.content, (768, 432));
        assert_eq!(layout.origin, (16, 0));
        assert_eq!(layout.controls_height, 48);
        assert!(layout.key_at(15.0, 240.0).is_none());
        assert!(layout.key_at(400.0, 240.0).is_none());
        assert_eq!(layout.key_at(782.0, 431.0), Some(FlipperKey::Run));
        assert_eq!(layout.key_at(400.0, 455.0), Some(FlipperKey::Power));
        assert!(layout.key_at(400.0, 433.0).is_none());
        let controls: Vec<_> = layout.controls().collect();
        assert_eq!(
            controls.iter().map(|&(_, _, label)| label).collect::<Vec<_>>(),
            ["Z", "X", "C", "V", "B"]
        );
        for (button, key, _) in controls {
            let x = f64::from(button.x + button.w / 2);
            assert_eq!(layout.key_at(x, 455.0), Some(key));
            assert_eq!(layout.key_at(x, 431.0), Some(key));
            assert!(layout.key_at(f64::from(button.x - 1), 455.0).is_none());
        }
    }

    #[test]
    fn fits_portrait_small_and_scale_limited_surfaces() {
        let portrait = Layout::new((480, 800), PANEL, None);
        assert_eq!(portrait.content, PANEL);
        assert_eq!(portrait.origin, (112, 296));
        let native = Layout::new(PANEL, PANEL, None);
        assert_eq!(native.controls().count(), 0);
        assert_eq!(native.key_at(128.0, 72.0), Some(FlipperKey::Ok));
        let tiny = Layout::new((128, 128), PANEL, None);
        assert_eq!(tiny.content, (128, 72));
        assert_eq!(tiny.key_at(64.0, 64.0), Some(FlipperKey::Ok));
        let limited = Layout::new((1920, 1080), PANEL, Some(2));
        assert_eq!(limited.content, (512, 288));
    }
}
