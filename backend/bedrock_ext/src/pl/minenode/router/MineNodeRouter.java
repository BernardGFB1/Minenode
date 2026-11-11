package pl.minenode.router;

import org.geysermc.event.subscribe.Subscribe;
import org.geysermc.geyser.api.event.bedrock.SessionLoginEvent;
import org.geysermc.geyser.api.extension.Extension;
import org.geysermc.geyser.api.network.RemoteServer;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * Geyser extension that routes each Bedrock login to the MineNode server whose public hostname the
 * player used, and applies that server's online/offline authentication mode.
 *
 * <p>The Java proxy in front of the Minecraft servers (and its hostname routing) is untouched:
 * every route normally points at the proxy and Geyser forwards the original Bedrock hostname.</p>
 *
 * <p>{@code routes.properties} is re-read when its modification time or size changes, so enabling
 * or disabling Bedrock for a server does not require restarting Geyser.</p>
 */
public final class MineNodeRouter implements Extension {
    public static final String VERSION = "1.0.6";
    private static final String ROUTES_FILE = "routes.properties";
    private static final long CHECK_INTERVAL_MS = 1_000L;

    private volatile RouteTable table = RouteTable.empty();
    private volatile Path routesPath;
    private volatile long loadedMtime = Long.MIN_VALUE;
    private volatile long loadedSize = -1L;
    private volatile long checkedAt = Long.MIN_VALUE;
    private volatile boolean warnedMissing;

    /**
     * Extension instances must not touch {@link #dataFolder()} or {@link #logger()} in the
     * constructor: Geyser registers the extension container only after construction.
     */
    public MineNodeRouter() {
    }

    @Subscribe
    public void onSessionLogin(SessionLoginEvent event) {
        logInfo("MineNodeRouter version " + VERSION + " obsługuje logowanie Bedrock");
        RouteTable current;
        try {
            current = routes();
        } catch (RuntimeException exc) {
            logError("Nie można odczytać routes.properties", exc);
            event.setCancelled(true, RouteTable.DEFAULT_DENIED_MESSAGE);
            return;
        }

        String host;
        try {
            host = RouteTable.normalizeHost(event.connection().joinAddress());
        } catch (RuntimeException exc) {
            logWarning("Nie można ustalić nazwy hosta gracza: " + exc);
            event.setCancelled(true, current.deniedMessage());
            return;
        }

        RemoteServer original = event.remoteServer();
        logInfo("Diagnostyka Bedrock: joinAddress='" + event.connection().joinAddress()
                + "', host='" + host + "', routes=" + current.size()
                + ", hosts=" + current.hosts()
                + ", denied=" + current.isDenied(host)
                + ", fallback=" + routeDescription(current.fallback())
                + ", remote=" + routeDescription(original));

        RouteTable.Route route = current.route(host);
        if (route == null) {
            if (current.isDenied(host)) {
                // The host belongs to a server with Bedrock turned off: without this the Java proxy
                // would still route the forwarded hostname to that server.
                logInfo("Odrzucono Bedrock " + event.connection().bedrockUsername() + ": host " + host
                        + " należy do serwera bez włączonego Bedrocka");
                event.setCancelled(true, current.deniedMessage());
                return;
            }
            logInfo("Odrzucono Bedrock " + event.connection().bedrockUsername() + ": nieznany host '" + host + "'");
            event.setCancelled(true, current.deniedMessage());
            return;
        }

        logInfo("Wybrano trasę Bedrock: host='" + host + "' -> " + routeDescription(route));
        event.remoteServer(new RouteRemoteServer(original, route));
        logger().debug("Bedrock " + event.connection().bedrockUsername() + " → " + host
                + " → " + route.serverId() + " (" + route.address() + ":" + route.port()
                + ", " + (route.online() ? "online" : "offline") + ")");
    }

    private RouteTable routes() {
        long now = System.currentTimeMillis();
        if (RouteTable.routeCacheFresh(now, checkedAt)) {
            return table;
        }
        checkedAt = now;

        Path path = routesPath();
        if (!Files.isRegularFile(path)) {
            if (!warnedMissing) {
                warnedMissing = true;
                logWarning("Brak pliku " + path + " – wszystkie połączenia Bedrock będą odrzucane");
            }
            loadedMtime = Long.MIN_VALUE;
            loadedSize = -1L;
            table = RouteTable.empty();
            return table;
        }

        long mtime;
        long size;
        try {
            mtime = Files.getLastModifiedTime(path).toMillis();
            size = Files.size(path);
        } catch (IOException exc) {
            throw new IllegalStateException("Nie można sprawdzić " + path, exc);
        }
        if (mtime == loadedMtime && size == loadedSize) {
            return table;
        }

        List<String> lines;
        try {
            lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        } catch (IOException exc) {
            throw new IllegalStateException("Nie można odczytać " + path, exc);
        }
        RouteTable parsed = RouteTable.parse(lines);
        table = parsed;
        loadedMtime = mtime;
        loadedSize = size;
        warnedMissing = false;
        logger().info("Wczytano " + parsed.size() + " tras Bedrock z " + path);
        return parsed;
    }

    private Path routesPath() {
        Path extensionData = dataFolder();
        List<Path> candidates = findRouteFiles(extensionData);
        Path path = candidates.isEmpty() ? extensionData.resolve(ROUTES_FILE) : candidates.get(0);
        if (!path.equals(routesPath)) {
            routesPath = path;
            logInfo("MineNodeRouter version " + VERSION + " aktywny");
            logInfo("Znalezione pliki tras: " + candidates);
            logger().info("MineNodeRouter używa pliku tras: " + path);
            loadedMtime = Long.MIN_VALUE;
            loadedSize = -1L;
        }
        return path;
    }

    private List<Path> findRouteFiles(Path extensionData) {
        List<Path> found = new ArrayList<>();
        addCandidate(found, extensionData.resolve(ROUTES_FILE));
        addCandidate(found, extensionData.resolve("minenode-router").resolve(ROUTES_FILE));
        addCandidate(found, extensionData.resolve("../minenode-router").normalize().resolve(ROUTES_FILE));
        addCandidate(found, extensionData.resolve("../../extensions/minenode-router").normalize()
                .resolve(ROUTES_FILE));
        addCandidate(found, Path.of("/data/bedrock/extensions/minenode-router", ROUTES_FILE));
        String configuredData = System.getenv("MINENODE_DATA");
        if (configuredData != null && !configuredData.isBlank()) {
            addCandidate(found, Path.of(configuredData, "bedrock", "extensions", "minenode-router", ROUTES_FILE));
        }
        found.sort(Comparator.comparingLong(this::routeFileScore).reversed());
        return found;
    }

    private void addCandidate(List<Path> candidates, Path path) {
        Path normalized = path.toAbsolutePath().normalize();
        if (Files.isRegularFile(normalized) && !candidates.contains(normalized)) {
            candidates.add(normalized);
        }
    }

    private long routeFileScore(Path path) {
        try {
            // Generated files with route.N/fallback entries are larger than an empty metadata copy.
            return Files.size(path);
        } catch (IOException ignored) {
            return -1L;
        }
    }

    private void logInfo(String message) {
        try {
            logger().info(message);
        } catch (RuntimeException ignored) {
            // Logging must never break a login.
        }
    }

    private void logWarning(String message) {
        try {
            logger().warning(message);
        } catch (RuntimeException ignored) {
            // Logging must never break a login.
        }
    }

    private void logError(String message, Throwable exc) {
        try {
            logger().error(message, exc);
        } catch (RuntimeException ignored) {
            // Logging must never break a login.
        }
    }

    private String routeDescription(RouteTable.Route route) {
        if (route == null) {
            return "none";
        }
        return route.host() + "|" + route.serverId() + "|"
                + (route.online() ? "online" : "offline") + "|"
                + route.address() + ":" + route.port();
    }

    private String routeDescription(RemoteServer remote) {
        if (remote == null) {
            return "none";
        }
        return remote.address() + ":" + remote.port() + "/" + remote.authType();
    }
}
