//! Switch the lab's dcttech USBRelay4 (16c0:05df) at /dev/lab-relay.
//!
//! "on" energises a coil: NO closes and NC opens. Every command prints the
//! coil states when it finishes.

use std::fs::{File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::process::ExitCode;
use std::time::{Duration, Instant};

const DEVICE: &str = "/dev/lab-relay";
const USAGE: &str = "usage: lab-relay status | on N | off N | pulse N [SECONDS]";

/// `_IOC(_IOC_READ | _IOC_WRITE, 'H', nr, 9)` from linux/hidraw.h, sized for one report.
const fn hidioc(nr: libc::c_ulong) -> libc::c_ulong {
    (3 << 30) | (9 << 16) | ((b'H' as libc::c_ulong) << 8) | nr
}
const HIDIOCSFEATURE: libc::c_ulong = hidioc(0x06);
const HIDIOCGFEATURE: libc::c_ulong = hidioc(0x07);

/// Feature report 0 as hidraw takes it: the report ID, FF (on) or FD (off),
/// then the 1-based channel.
fn switch_report(channel: u8, on: bool) -> [u8; 9] {
    [0, if on { 0xFF } else { 0xFD }, channel, 0, 0, 0, 0, 0, 0]
}

/// The report ID and a 5-character board ID precede the data; its byte 7 is
/// the energised-coil bitmask, bit 0 for channel 1.
fn coil_states(report: &[u8; 9]) -> [bool; 4] {
    std::array::from_fn(|n| report[8] >> n & 1 == 1)
}

#[derive(Debug, PartialEq)]
enum Command {
    Status,
    Switch(u8, bool),
    Pulse(u8, Duration),
}

fn parse(args: &[&str]) -> Option<Command> {
    let channel = |arg: &str| arg.parse::<u8>().ok().filter(|n| (1..=4).contains(n));
    let length = |arg: &str| Duration::try_from_secs_f64(arg.parse().ok()?).ok();
    match *args {
        ["status"] => Some(Command::Status),
        ["on", n] => Some(Command::Switch(channel(n)?, true)),
        ["off", n] => Some(Command::Switch(channel(n)?, false)),
        ["pulse", n] => Some(Command::Pulse(channel(n)?, Duration::from_secs(1))),
        ["pulse", n, seconds] => Some(Command::Pulse(channel(n)?, length(seconds)?)),
        _ => None,
    }
}

struct Relay(File);

impl Relay {
    fn open() -> io::Result<Self> {
        let file = OpenOptions::new().read(true).write(true).open(DEVICE);
        file.map(Self)
            .map_err(|error| io::Error::new(error.kind(), format!("{DEVICE}: {error}")))
    }

    fn feature(&self, request: libc::c_ulong, report: &mut [u8; 9]) -> io::Result<()> {
        // SAFETY: the request encodes the 9-byte size, which bounds what the kernel touches.
        let ret = unsafe { libc::ioctl(self.0.as_raw_fd(), request as _, report.as_mut_ptr()) };
        if ret < 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }

    fn coils(&self) -> io::Result<[bool; 4]> {
        let mut report = [0; 9];
        self.feature(HIDIOCGFEATURE, &mut report)?;
        Ok(coil_states(&report))
    }

    /// Switches a coil and checks the module's own report of it.
    fn switch(&self, channel: u8, on: bool) -> io::Result<()> {
        self.feature(HIDIOCSFEATURE, &mut switch_report(channel, on))?;
        if self.coils()?[usize::from(channel - 1)] != on {
            let state = if on { "on" } else { "off" };
            return Err(io::Error::other(format!(
                "channel {channel} did not switch {state}"
            )));
        }
        Ok(())
    }

    /// Energises a coil for `length` or until SIGINT, SIGTERM or SIGHUP, then
    /// releases it. Those signals stay blocked from before the coil closes, so
    /// only SIGKILL can leave it on. Returns the signal that ended the pulse.
    fn pulse(&self, channel: u8, length: Duration) -> io::Result<Option<i32>> {
        let signals = block_signals();
        let held = self.switch(channel, true).map(|()| wait(&signals, length));
        let released = self.switch(channel, false);
        let signal = held?;
        released?;
        Ok(signal)
    }
}

fn block_signals() -> libc::sigset_t {
    // SAFETY: sigset_t is plain data and sigemptyset initialises it before use.
    unsafe {
        let mut set = std::mem::zeroed();
        libc::sigemptyset(&mut set);
        for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
            libc::sigaddset(&mut set, signal);
        }
        libc::pthread_sigmask(libc::SIG_BLOCK, &set, std::ptr::null_mut());
        set
    }
}

/// Waits up to `length` for one of the blocked `signals` and returns it.
fn wait(signals: &libc::sigset_t, length: Duration) -> Option<i32> {
    let deadline = Instant::now() + length;
    while let Some(left) = deadline.checked_duration_since(Instant::now()) {
        let timeout = libc::timespec {
            tv_sec: left.as_secs() as _,
            tv_nsec: left.subsec_nanos() as _,
        };
        // SAFETY: both pointers are valid for the call, and siginfo may be null.
        match unsafe { libc::sigtimedwait(signals, std::ptr::null_mut(), &timeout) } {
            -1 if io::Error::last_os_error().raw_os_error() == Some(libc::EINTR) => continue,
            -1 => return None,
            signal => return Some(signal),
        }
    }
    None
}

fn execute(command: Command) -> io::Result<ExitCode> {
    let relay = Relay::open()?;
    let mut code = ExitCode::SUCCESS;
    match command {
        Command::Status => {}
        Command::Switch(channel, on) => relay.switch(channel, on)?,
        Command::Pulse(channel, length) => {
            if let Some(signal) = relay.pulse(channel, length)? {
                eprintln!("lab-relay: signal {signal} ended the pulse early; coil released");
                code = ExitCode::from(128 + signal as u8);
            }
        }
    }
    let states: Vec<String> = (relay.coils()?.iter().enumerate())
        .map(|(n, &on)| format!("{}:{}", n + 1, if on { "on" } else { "off" }))
        .collect();
    println!("{}", states.join(" "));
    Ok(code)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let Some(command) = parse(&args) else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
    execute(command).unwrap_or_else(|error| {
        eprintln!("lab-relay: {error}");
        ExitCode::FAILURE
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ioctl_numbers_match_linux_hidraw_h() {
        assert_eq!(HIDIOCSFEATURE, 0xC009_4806);
        assert_eq!(HIDIOCGFEATURE, 0xC009_4807);
    }

    #[test]
    fn reports() {
        assert_eq!(switch_report(2, true), [0, 0xFF, 2, 0, 0, 0, 0, 0, 0]);
        assert_eq!(switch_report(1, false), [0, 0xFD, 1, 0, 0, 0, 0, 0, 0]);
        let report = [0, b'Q', b'A', b'A', b'M', b'Z', 0, 0, 0b0101];
        assert_eq!(coil_states(&report), [true, false, true, false]);
    }

    #[test]
    fn arguments() {
        assert_eq!(parse(&["status"]), Some(Command::Status));
        assert_eq!(parse(&["off", "4"]), Some(Command::Switch(4, false)));
        assert_eq!(
            parse(&["pulse", "1"]),
            Some(Command::Pulse(1, Duration::from_secs(1)))
        );
        let half = Duration::from_millis(500);
        assert_eq!(parse(&["pulse", "1", "0.5"]), Some(Command::Pulse(1, half)));
        for bad in [
            &["on", "5"][..],
            &["on"],
            &["pulse", "1", "-1"],
            &["status", "1"],
        ] {
            assert_eq!(parse(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn blocked_signal_ends_the_wait() {
        let signals = block_signals();
        assert_eq!(wait(&signals, Duration::from_millis(20)), None);
        // SAFETY: raise() targets this thread, where the signal is blocked.
        unsafe { libc::raise(libc::SIGTERM) };
        let start = Instant::now();
        assert_eq!(wait(&signals, Duration::from_secs(5)), Some(libc::SIGTERM));
        assert!(start.elapsed() < Duration::from_secs(1));
    }
}
