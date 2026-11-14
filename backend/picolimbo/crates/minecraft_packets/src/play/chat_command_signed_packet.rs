use minecraft_protocol::prelude::*;

/// Signed command packet used by 1.21+ Java clients and by Geyser for Bedrock
/// commands. Only the command text is needed by the lobby; the signature,
/// timestamp and acknowledgement data are ignored.
#[derive(PacketIn)]
pub struct ChatCommandSignedPacket {
    command: String,
}

impl ChatCommandSignedPacket {
    pub fn get_command(&self) -> &str {
        &self.command
    }
}
