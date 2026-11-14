use crate::auth::ResultKind;
use crate::handlers::configuration::send_message;
use crate::handlers::play::set_player_position_and_rotation::teleport_player_to_spawn;
use crate::server::batch::Batch;
use crate::server::client_state::ClientState;
use crate::server::packet_handler::{PacketHandler, PacketHandlerError};
use crate::server::packet_registry::PacketRegistry;
use crate::server_state::{ServerCommand, ServerCommands, ServerState};
use minecraft_packets::play::chat_command_packet::ChatCommandPacket;
use minecraft_packets::play::chat_command_signed_packet::ChatCommandSignedPacket;
use minecraft_packets::play::chat_message_packet::ChatMessagePacket;
use minecraft_packets::play::client_bound_player_abilities_packet::ClientBoundPlayerAbilitiesPacket;
use minecraft_packets::play::transfer_packet::TransferPacket;
use minecraft_protocol::prelude::{ProtocolVersion, VarInt};
use pico_text_component::prelude::Component;
use thiserror::Error;
use tracing::{info, warn};

impl PacketHandler for ChatCommandPacket {
    fn handle(
        &self,
        client_state: &mut ClientState,
        server_state: &ServerState,
    ) -> Result<Batch, PacketHandlerError> {
        let mut batch = Batch::new();
        run_command(client_state, server_state, self.get_command(), &mut batch);
        Ok(batch)
    }
}

impl PacketHandler for ChatCommandSignedPacket {
    fn handle(
        &self,
        client_state: &mut ClientState,
        server_state: &ServerState,
    ) -> Result<Batch, PacketHandlerError> {
        let mut batch = Batch::new();
        run_command(
            client_state,
            server_state,
            self.get_command(),
            &mut batch,
        );
        Ok(batch)
    }
}

impl PacketHandler for ChatMessagePacket {
    fn handle(
        &self,
        client_state: &mut ClientState,
        server_state: &ServerState,
    ) -> Result<Batch, PacketHandlerError> {
        let mut batch = Batch::new();
        // Never log the message body: a Bedrock client can deliver
        // "login <password>" as ordinary chat, and the same applies to a
        // command typed without the leading slash.
        info!("{} sent a chat message", client_state.get_username());
        if let Some(command) = self.get_command().or_else(|| auth_command(self.get_message())) {
            run_command(client_state, server_state, command, &mut batch);
        }
        Ok(batch)
    }
}

/// Command text safe for logging. Authentication arguments are passwords and
/// must never reach the backend logs.
fn command_label(command: &str) -> &str {
    let name = command
        .trim_start_matches('/')
        .split_whitespace()
        .next()
        .unwrap_or("");
    if name.eq_ignore_ascii_case("login") {
        "login"
    } else if name.eq_ignore_ascii_case("register") {
        "register"
    } else {
        command
    }
}

/// Geyser normally translates Bedrock commands to ChatCommandPacket. Some
/// protocol/client combinations deliver them as a normal chat message,
/// however, without the leading slash. Accept only the two auth command
/// prefixes here; ordinary chat is never interpreted as an auth command.
fn auth_command(message: &str) -> Option<&str> {
    let command = message.strip_prefix('/').unwrap_or(message);
    let name = command.split_whitespace().next()?;
    if name.eq_ignore_ascii_case("login") || name.eq_ignore_ascii_case("register") {
        Some(command)
    } else {
        None
    }
}

fn run_command(
    client_state: &mut ClientState,
    server_state: &ServerState,
    command: &str,
    batch: &mut Batch,
) {
    info!(
        "{} issued server command: /{}",
        client_state.get_username(),
        command_label(command)
    );

    if let Some(result) = crate::auth::command(&client_state.get_username(), command) {
        match result {
            ResultKind::Message(message) => {
                send_message(batch, &Component::new(message), client_state.protocol_version());
            }
            ResultKind::Success(message) => client_state.kick(&message),
        }
        return;
    }

    if let Ok(parsed_command) = Command::parse(server_state.server_commands(), command) {
        match parsed_command {
            Command::Spawn => {
                teleport_player_to_spawn(client_state, server_state, batch);
            }
            Command::Fly => {
                let allow_flying = !client_state.is_flight_allowed();
                let flying = allow_flying && client_state.is_flying();
                let packet = ClientBoundPlayerAbilitiesPacket::builder()
                    .allow_flying(allow_flying)
                    .flying(flying)
                    .flying_speed(client_state.get_flying_speed())
                    .build();
                batch.queue(|| PacketRegistry::ClientBoundPlayerAbilities(packet));
                client_state.set_is_flight_allowed(allow_flying);
                client_state.set_is_flying(allow_flying);
            }
            Command::FlySpeed(speed) => {
                let packet = ClientBoundPlayerAbilitiesPacket::builder()
                    .allow_flying(client_state.is_flight_allowed())
                    .flying(client_state.is_flying())
                    .flying_speed(speed)
                    .build();
                batch.queue(|| PacketRegistry::ClientBoundPlayerAbilities(packet));
                client_state.set_flying_speed(speed);
            }
            Command::Transfer(host, port) => {
                if client_state
                    .protocol_version()
                    .is_after_inclusive(ProtocolVersion::V1_20_5)
                {
                    info!(
                        "Transferring {} to {}:{}",
                        client_state.get_username(),
                        host,
                        port
                    );
                    let packet = TransferPacket {
                        host,
                        port: VarInt::from(port),
                    };
                    batch.queue(|| PacketRegistry::Transfer(packet));
                } else {
                    warn!(
                        "{} tried to transfer servers on unsupported version {}",
                        client_state.get_username(),
                        client_state.protocol_version().humanize()
                    );
                }
            }
        }
    }
}

#[derive(Debug, Error)]
pub enum ParseCommandError {
    #[error("empty command")]
    Empty,
    #[error("unknown command")]
    Unknown,
    #[error("invalid speed value")]
    InvalidSpeed(#[from] std::num::ParseFloatError),
    #[error("invalid hostname")]
    InvalidHost,
    #[error("invalid port")]
    InvalidPort(#[from] std::num::ParseIntError),
}

enum Command {
    Spawn,
    Fly,
    FlySpeed(f32),
    Transfer(String, i32),
}

impl Command {
    pub fn parse(server_commands: &ServerCommands, input: &str) -> Result<Self, ParseCommandError> {
        let mut parts = input.split_whitespace();
        let cmd = parts.next().ok_or(ParseCommandError::Empty)?;
        if Self::is_command(server_commands.spawn(), cmd) {
            Ok(Self::Spawn)
        } else if Self::is_command(server_commands.fly(), cmd) {
            Ok(Self::Fly)
        } else if Self::is_command(server_commands.fly_speed(), cmd) {
            let speed_str = parts.next().unwrap_or("0.05");
            let speed = speed_str.parse::<f32>()?.clamp(0.0, 1.0);
            Ok(Self::FlySpeed(speed))
        } else if Self::is_command(server_commands.transfer(), cmd) {
            let host = parts
                .next()
                .ok_or(ParseCommandError::InvalidHost)?
                .to_string();
            let port_str = parts.next().unwrap_or("25565");
            let port = port_str.parse::<i32>()?;
            Ok(Self::Transfer(host, port))
        } else {
            Err(ParseCommandError::Unknown)
        }
    }

    fn is_command(server_command: ServerCommand, command: &str) -> bool {
        if let ServerCommand::Enabled { alias } = server_command
            && command == alias
        {
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{auth_command, command_label};

    #[test]
    fn bedrock_chat_auth_commands_are_recognized() {
        assert_eq!(auth_command("/login secret"), Some("login secret"));
        assert_eq!(auth_command("login secret"), Some("login secret"));
        assert_eq!(auth_command("LOGIN secret"), Some("LOGIN secret"));
        assert_eq!(auth_command("register one two"), Some("register one two"));
        assert_eq!(auth_command("hello"), None);
        assert_eq!(auth_command("/say login"), None);
    }

    #[test]
    fn auth_arguments_are_never_logged() {
        assert_eq!(command_label("login secret"), "login");
        assert_eq!(command_label("/login secret"), "login");
        assert_eq!(command_label("REGISTER one two"), "register");
        assert_eq!(command_label("spawn"), "spawn");
    }
}
