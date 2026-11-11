package pl.minenode.router;

import org.geysermc.event.subscribe.Subscribe;
import org.geysermc.geyser.api.event.bedrock.SessionLoginEvent;
import org.geysermc.geyser.api.extension.Extension;
import org.geysermc.geyser.api.network.RemoteServer;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
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
            route = current.fallback();
            if (route == null) {
                logInfo("Odrzucono Bedrock " + event.connection().bedrockUsername() + ": nieznany host '" + host
                        + "' i brak serwera domyślnego z włączonym Bedrockiem");
                event.setCancelled(true, current.deniedMessage());
                return;
            }
            logInfo("Bedrock " + event.connection().bedrockUsername() + ": nieznany host '" + host
                    + "' → serwer domyślny " + route.serverId());
        }

        RemoteServer original = event.remoteServer();
        event.remoteServer(new RouteRemoteServer(original, route));
        logger().debug("Bedrock " + event.connection().bedrockUsername() + " → " + host
                + " → " + route.serverId() + " (" + route.address() + ":" + route.port()
                + ", " + (route.online() ? "online" : "offline") + ")");
    }

    private RouteTable routes() {
        long now = System.currentTimeMillis();
        if (now - checkedAt < CHECK_INTERVAL_MS) {
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
        Path direct = extensionData.resolve(ROUTES_FILE);
        // Geyser versions/platforms have used both the extension data folder itself and
        // its parent extension directory as the extension dataFolder(). MineNode writes to
        // extensions/minenode-router/routes.properties, so accept both layouts.
        Path sibling = extensionData.resolve("../minenode-router").normalize().resolve(ROUTES_FILE);
        Path path = newestRoutesFile(direct, sibling);
        if (!path.equals(routesPath)) {
            routesPath = path;
            logger().info("MineNodeRouter używa pliku tras: " + path);
            loadedMtime = Long.MIN_VALUE;
            loadedSize = -1L;
        }
        return path;
    }

    private Path newestRoutesFile(Path first, Path second) {
        boolean firstExists = Files.isRegularFile(first);
        boolean secondExists = Files.isRegularFile(second);
        if (!firstExists && secondExists) {
            return second;
        }
        if (firstExists && secondExists) {
            try {
                return Files.getLastModifiedTime(second).compareTo(Files.getLastModifiedTime(first)) > 0
                        ? second : first;
            } catch (IOException ignored) {
                // Prefer the normal extension data path if metadata cannot be read.
            }
        }
        return first;
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
}
