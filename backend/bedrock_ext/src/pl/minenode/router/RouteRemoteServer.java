package pl.minenode.router;

import org.geysermc.geyser.api.network.AuthType;
import org.geysermc.geyser.api.network.RemoteServer;

/**
 * A Java target chosen from the route table, preserving the protocol information from Geyser's
 * configured remote server.
 */
final class RouteRemoteServer implements RemoteServer {
    private final RemoteServer delegate;
    private final String address;
    private final int port;
    private final AuthType authType;

    RouteRemoteServer(RemoteServer delegate, RouteTable.Route route) {
        this.delegate = delegate;
        this.address = route.address();
        this.port = route.port();
        this.authType = route.online() ? AuthType.ONLINE : AuthType.OFFLINE;
    }

    @Override
    public String address() {
        return address;
    }

    @Override
    public int port() {
        return port;
    }

    @Override
    public int protocolVersion() {
        return delegate == null ? 0 : delegate.protocolVersion();
    }

    @Override
    public String minecraftVersion() {
        return delegate == null ? "" : delegate.minecraftVersion();
    }

    @Override
    public AuthType authType() {
        return authType;
    }

    @Override
    public boolean resolveSrv() {
        return false;
    }
}
