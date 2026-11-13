package pl.minenode.auth;

import org.bukkit.ChatColor;
import org.bukkit.GameMode;
import org.bukkit.World;
import org.bukkit.Bukkit;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.plugin.java.JavaPlugin;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Minimal password plugin for the hidden MineNode authentication lobby. */
public final class MineNodeAuth extends JavaPlugin implements Listener {
    private static final long TTL = 30 * 60;
    private static final long PENDING_TTL = 30 * 60;
    private static final int ITERATIONS = 120_000;
    private final SecureRandom random = new SecureRandom();
    private final Map<String, String> realIps = new ConcurrentHashMap<>();
    private Path file;
    private Path pending;

    @Override public void onEnable() {
        String root = System.getenv().getOrDefault("MINENODE_DATA", "/data");
        file = Path.of(root, "auth", "sessions.db");
        pending = Path.of(root, "auth", "pending.db");
        try { Files.createDirectories(file.getParent()); if (!Files.exists(file)) Files.createFile(file); }
        catch (IOException e) { getLogger().severe("Nie można otworzyć bazy MineNodeAuth: " + e); }
        getServer().getPluginManager().registerEvents(this, this);
        World world = Bukkit.getWorld("auth");
        if (world != null) {
            world.setDifficulty(org.bukkit.Difficulty.PEACEFUL);
            world.setTime(6000);
            world.setGameRuleValue("doDaylightCycle", "false");
            world.setGameRuleValue("doWeatherCycle", "false");
            world.setGameRuleValue("doMobSpawning", "false");
            world.setGameRuleValue("randomTickSpeed", "0");
        }
    }

    @EventHandler public void join(PlayerJoinEvent event) {
        event.setJoinMessage(null);
        Player p = event.getPlayer();
        // Resolve the real client IP immediately after the proxy wrote it.
        // Keeping it in memory avoids a 127.0.0.1 fallback if the player
        // spends a while in the lobby before using /register or /login.
        String realIp = pendingIp(p);
        if (realIp != null) realIps.put(p.getName().toLowerCase(Locale.ROOT), realIp);
        p.setGameMode(GameMode.SPECTATOR);
        p.getInventory().clear();
        World world = Bukkit.getWorld("auth");
        if (world != null) p.teleport(world.getSpawnLocation());
        p.sendMessage(ChatColor.YELLOW + "MineNode: zaloguj się przez /login <hasło> lub zarejestruj przez /register <hasło> <hasło>");
    }

    @EventHandler public void quit(PlayerQuitEvent event) {
        realIps.remove(event.getPlayer().getName().toLowerCase(Locale.ROOT));
    }

    @Override public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!(sender instanceof Player p)) return true;
        String name = p.getName().toLowerCase(Locale.ROOT);
        String ip = ip(p);
        if (command.getName().equalsIgnoreCase("register")) {
            if (args.length != 2 || !args[0].equals(args[1]) || args[0].length() < 6) {
                p.sendMessage(ChatColor.RED + "Użycie: /register <hasło min. 6 znaków> <powtórz hasło>"); return true;
            }
            synchronized (this) {
                List<String[]> rows = read();
                if (find(rows, name) != null) { p.sendMessage(ChatColor.RED + "Ten nick jest już zarejestrowany. Użyj /login."); return true; }
                String salt = bytes(16), hash = hash(args[0], salt);
                rows.add(new String[]{name, salt, hash, ip, Long.toString(expiry())});
                write(rows);
            }
            kick(p, "Zarejestrowano i zalogowano. Połącz się ponownie.");
            return true;
        }
        if (command.getName().equalsIgnoreCase("login")) {
            if (args.length != 1) { p.sendMessage(ChatColor.RED + "Użycie: /login <hasło>"); return true; }
            synchronized (this) {
                String[] row = find(read(), name);
                if (row == null || !hash(args[0], row[1]).equals(row[2])) {
                    p.sendMessage(ChatColor.RED + "Nieprawidłowy nick lub hasło."); return true;
                }
                row[3] = ip; row[4] = Long.toString(expiry()); write(readReplace(row));
            }
            kick(p, "Zalogowano. Połącz się ponownie.");
            return true;
        }
        return false;
    }

    private void kick(Player p, String message) { getServer().getScheduler().runTask(this, () -> p.kickPlayer(ChatColor.GREEN + message)); }
    private long expiry() { return System.currentTimeMillis() / 1000 + TTL; }
    private String ip(Player p) {
        String cached = realIps.get(p.getName().toLowerCase(Locale.ROOT));
        if (cached != null) return cached;
        String fromPending = pendingIp(p);
        if (fromPending != null) return fromPending;
        if (p.getAddress() instanceof InetSocketAddress a && a.getAddress() != null) return a.getAddress().getHostAddress();
        return "unknown";
    }

    /** Read the real IP written by the proxy when it routed the player to this lobby. */
    private String pendingIp(Player p) {
        try {
            double now = System.currentTimeMillis() / 1000.0;
            for (String line : Files.readAllLines(pending, StandardCharsets.UTF_8)) {
                String[] parts = line.split("\\t", -1);
                if (parts.length == 3 && parts[0].equalsIgnoreCase(p.getName())
                        && now - Double.parseDouble(parts[2]) < PENDING_TTL) return parts[1];
            }
        } catch (Exception ignored) { }
        return null;
    }
    private String bytes(int n) { byte[] b = new byte[n]; random.nextBytes(b); return HexFormat.of().formatHex(b); }
    private String hash(String password, String salt) {
        try {
            PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), HexFormat.of().parseHex(salt), ITERATIONS, 256);
            return HexFormat.of().formatHex(SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded());
        } catch (Exception e) { throw new IllegalStateException(e); }
    }
    private List<String[]> read() {
        List<String[]> out = new ArrayList<>();
        try { for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) { String[] r = line.split("\\t", -1); if (r.length == 5) out.add(r); } }
        catch (IOException ignored) { }
        return out;
    }
    private String[] find(List<String[]> rows, String name) { return rows.stream().filter(r -> r[0].equalsIgnoreCase(name)).findFirst().orElse(null); }
    private List<String[]> readReplace(String[] replacement) {
        List<String[]> rows = read(); String[] old = find(rows, replacement[0]);
        if (old == null) rows.add(replacement); else { old[1] = replacement[1]; old[2] = replacement[2]; old[3] = replacement[3]; old[4] = replacement[4]; }
        return rows;
    }
    private void write(List<String[]> rows) {
        try {
            Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
            List<String> lines = rows.stream().map(r -> String.join("\t", r)).toList();
            Files.write(tmp, lines, StandardCharsets.UTF_8);
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) { getLogger().severe("Nie można zapisać sesji: " + e); }
    }
}
