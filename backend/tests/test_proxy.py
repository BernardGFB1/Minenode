from app.proxy import plain_text


def test_kick_messages_lose_formatting_codes():
    assert plain_text("§csurvival jest wyłączony.") == "survival jest wyłączony."
    assert plain_text("§x§1§2§3§4§5§6Hi\n§7Dołącz ponownie") == "Hi\nDołącz ponownie"
    assert plain_text("zwykły tekst") == "zwykły tekst"
