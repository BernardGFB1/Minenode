//! MineNode's deliberately small, file-compatible lobby authentication.
//! Registration is create-only; existing password material is never changed
//! by PicoLimbo. Password changes remain a panel-only operation.
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use subtle::ConstantTimeEq;
use tracing::{info, warn};

type HmacSha256 = Hmac<Sha256>;
const ITERATIONS: u32 = 120_000;
const SESSION_TTL: i64 = 30 * 60;
const PENDING_TTL: i64 = 30 * 60;

fn root() -> PathBuf {
    std::env::var_os("MINENODE_DATA").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/data"))
}
fn file() -> PathBuf { root().join("auth/sessions.db") }
fn pending_file() -> PathBuf { root().join("auth/pending.db") }
fn now() -> i64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() as i64 }
fn normalize(name: &str) -> String { name.to_lowercase() }

fn pbkdf2(password: &str, salt_hex: &str) -> Option<String> {
    let salt = hex::decode(salt_hex).ok()?;
    let mut input = salt;
    input.extend_from_slice(&1u32.to_be_bytes());
    let mut mac = HmacSha256::new_from_slice(password.as_bytes()).ok()?;
    mac.update(&input);
    let mut u = mac.finalize().into_bytes().to_vec();
    let mut out = u.clone();
    for _ in 1..ITERATIONS {
        let mut next = HmacSha256::new_from_slice(password.as_bytes()).ok()?;
        next.update(&u);
        u = next.finalize().into_bytes().to_vec();
        for (a, b) in out.iter_mut().zip(&u) { *a ^= *b; }
    }
    Some(hex::encode(out))
}

fn rows() -> Vec<Vec<String>> {
    fs::read_to_string(file()).unwrap_or_default().lines()
        .map(|line| line.split('\t').map(str::to_owned).collect())
        .filter(|row: &Vec<String>| row.len() >= 5 && !row[0].is_empty())
        .collect()
}
fn write(rows: &[Vec<String>]) -> Result<(), String> {
    let path = file();
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let tmp = path.with_extension("db.tmp");
    let text = rows.iter().map(|r| r.join("\t")).collect::<Vec<_>>().join("\n") + "\n";
    fs::write(&tmp, text).map_err(|e| e.to_string())?;
    fs::rename(tmp, path).map_err(|e| e.to_string())
}
fn pending_ip(name: &str) -> Option<String> {
    fs::read_to_string(pending_file()).unwrap_or_default().lines().find_map(|line| {
        let p: Vec<_> = line.split('\t').collect();
        if p.len() == 3 && p[0].eq_ignore_ascii_case(name) && p[2].parse::<i64>().ok().is_some_and(|t| now() - t < PENDING_TTL) {
            Some(p[1].to_owned())
        } else { None }
    })
}
fn random_salt() -> String {
    let mut bytes = [0u8; 16];
    getrandom::fill(&mut bytes).expect("OS random unavailable");
    hex::encode(bytes)
}

pub enum ResultKind { Message(String), Success(String) }

pub fn command(name: &str, command: &str) -> Option<ResultKind> {
    let mut parts = command.split_whitespace();
    let cmd = parts.next()?.trim_start_matches('/').to_lowercase();
    if cmd != "login" && cmd != "register" { return None; }
    let args: Vec<_> = parts.collect();
    let mut data = rows();
    let key = normalize(name);
    let index = data.iter().position(|row| row[0].eq_ignore_ascii_case(&key));
    let ip = pending_ip(name);
    if cmd == "register" {
        if args.len() != 2 || args[0] != args[1] || args[0].len() < 6 { return Some(ResultKind::Message("Użycie: /register <hasło min. 6 znaków> <powtórz hasło>".into())); }
        if index.is_some() { return Some(ResultKind::Message("Ten nick jest już zarejestrowany. Użyj /login.".into())); }
        let Some(ip) = ip else {
            warn!("MineNodeAuth: refusing /register for {} without a pending client IP", name);
            return Some(ResultKind::Message("Nie można ustalić adresu IP. Połącz się ponownie.".into()));
        };
        let salt = random_salt();
        let hash = pbkdf2(args[0], &salt).unwrap();
        data.push(vec![key, salt, hash, ip.clone(), (now() + SESSION_TTL).to_string()]);
        if let Err(err) = write(&data) {
            warn!("MineNodeAuth: could not save account {}: {}", name, err);
            return Some(ResultKind::Message("Nie udało się zapisać konta. Spróbuj ponownie.".into()));
        }
        info!("MineNodeAuth: registered {} with IP {}", name, ip);
        return Some(ResultKind::Success("Zarejestrowano i zalogowano. Połącz się ponownie.".into()));
    }
    if args.len() != 1 { return Some(ResultKind::Message("Użycie: /login <hasło>".into())); }
    let Some(i) = index else { return Some(ResultKind::Message("Nieprawidłowy nick lub hasło.".into())); };
    let Some(candidate) = pbkdf2(args[0], &data[i][1]) else { return Some(ResultKind::Message("Nieprawidłowy nick lub hasło.".into())); };
    if candidate.as_bytes().ct_eq(data[i][2].as_bytes()).unwrap_u8() != 1 { return Some(ResultKind::Message("Nieprawidłowy nick lub hasło.".into())); }
    match ip {
        Some(ip) => data[i][3] = ip,
        None if data[i][3].is_empty() || data[i][3] == "unknown" => {
            warn!("MineNodeAuth: refusing /login for {} without a pending client IP", name);
            return Some(ResultKind::Message("Nie można ustalić adresu IP. Połącz się ponownie.".into()));
        }
        None => warn!("MineNodeAuth: no pending IP for {}; keeping stored session IP", name),
    }
    data[i][4] = (now() + SESSION_TTL).to_string();
    // A successful reauthentication starts a new client-fingerprint binding.
    // Otherwise an old binding could invalidate this freshly authenticated
    // session when the proxy captures the client again.
    if data[i].len() > 5 { data[i][5].clear(); }
    if let Err(err) = write(&data) {
        warn!("MineNodeAuth: could not save session for {}: {}", name, err);
        return Some(ResultKind::Message("Nie udało się zapisać sesji. Spróbuj ponownie.".into()));
    }
    info!("MineNodeAuth: logged in {} from IP {}", name, data[i][3]);
    Some(ResultKind::Success("Zalogowano. Połącz się ponownie.".into()))
}

#[cfg(test)]
mod tests {
    #[test]
    fn password_algorithm_is_compatible_shape() {
        assert_eq!(super::ITERATIONS, 120_000);
        assert_eq!(super::pbkdf2("secret", &"00".repeat(16)).unwrap().len(), 64);
    }
}
