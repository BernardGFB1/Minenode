package pl.minenode.router;

import java.util.List;

/**
 * Dependency-free sanity tests for {@link RouteTable}. Run with {@code build.ps1 -Test} or
 * {@code build.sh --test}; no JUnit download is required.
 */
public final class RouteTableTest {
    private static int checks;

    private RouteTableTest() {
    }

    public static void main(String[] args) {
        parsesBasicRoutes();
        normalizesHosts();
        skipsInvalidEntries();
        respectsFirstDuplicate();
        handlesDeniedMessage();
        System.out.println("RouteTableTest: " + checks + " checks passed");
    }

    private static void parsesBasicRoutes() {
        RouteTable table = RouteTable.parse(List.of(
                "# generated",
                "",
                "denied-message=Nie ma takiego serwera",
                "route.0=survival.mc.example.com|abc123|online|127.0.0.1|25565",
                "route.1=Creative.MC.Example.COM.|def456|offline|127.0.0.1|25565"
        ));

        check(table.size() == 2, "two routes parsed");
        RouteTable.Route survival = table.route("survival.mc.example.com");
        check(survival != null, "survival route found");
        check(survival != null && survival.serverId().equals("abc123"), "server id parsed");
        check(survival != null && survival.online(), "online auth parsed");
        check(survival != null && survival.address().equals("127.0.0.1"), "address parsed");
        check(survival != null && survival.port() == 25565, "port parsed");

        RouteTable.Route creative = table.route("creative.mc.example.com");
        check(creative != null && !creative.online(), "offline auth parsed");
        check(creative != null && creative.serverId().equals("def456"), "offline server id parsed");
        check(creative != null && creative.host().equals("creative.mc.example.com"), "host normalized in route");
    }

    private static void normalizesHosts() {
        check(RouteTable.normalizeHost("  Play.Example.COM.  ").equals("play.example.com"), "trim/lowercase/dot");
        check(RouteTable.normalizeHost("play.example.com:19132").equals("play.example.com"), "host:port");
        check(RouteTable.normalizeHost("http://play.example.com:19132").equals("play.example.com"), "scheme removed");
        check(RouteTable.normalizeHost("https://play.example.com").equals("play.example.com"), "https removed");
        check(RouteTable.normalizeHost("[2001:db8::1]:19132").equals("2001:db8::1"), "bracketed ipv6");
        check(RouteTable.normalizeHost("2001:db8::1").equals("2001:db8::1"), "bare ipv6 preserved");
        check(RouteTable.normalizeHost("").equals(""), "empty host");
        check(RouteTable.normalizeHost(null).equals(""), "null host");
    }

    private static void skipsInvalidEntries() {
        RouteTable table = RouteTable.parse(List.of(
                "route.0=good.example.com|id|online|127.0.0.1|25565",
                "route.1=missing.example.com|id|online|127.0.0.1",
                "route.2=badport.example.com|id|online|127.0.0.1|99999",
                "route.3=badauth.example.com|id|floodgate|127.0.0.1|25565",
                "route.4=emptyhost.example.com||online|127.0.0.1|25565",
                "route.5=|id|online|127.0.0.1|25565",
                "route.6=noequal",
                "irrelevant=yes"
        ));
        check(table.size() == 1, "only valid routes retained");
        check(table.route("good.example.com") != null, "valid route retained");
        check(table.route("badport.example.com") == null, "invalid port rejected");
        check(table.route("badauth.example.com") == null, "invalid auth rejected");
        check(table.route("missing.example.com") == null, "incomplete route rejected");
    }

    private static void respectsFirstDuplicate() {
        RouteTable table = RouteTable.parse(List.of(
                "route.0=dup.example.com|first|online|127.0.0.1|25565",
                "route.1=dup.example.com|second|offline|127.0.0.1|25565"
        ));
        check(table.size() == 1, "duplicates collapse");
        check(table.route("dup.example.com").serverId().equals("first"), "first duplicate wins");
    }

    private static void handlesDeniedMessage() {
        check(RouteTable.empty().deniedMessage().equals(RouteTable.DEFAULT_DENIED_MESSAGE), "empty table default message");

        RouteTable custom = RouteTable.parse(List.of("denied-message=Linia 1\\nLinia 2 = nadal jedna wartość | z kreską"));
        check(custom.deniedMessage().equals("Linia 1\nLinia 2 = nadal jedna wartość | z kreską"), "denied message unescaped");

        RouteTable blank = RouteTable.parse(List.of("denied-message=   "));
        check(blank.deniedMessage().equals(RouteTable.DEFAULT_DENIED_MESSAGE), "blank denied message falls back");

        check(RouteTable.parse(List.of()).size() == 0, "empty input");
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) {
            throw new AssertionError("FAILED: " + message);
        }
    }
}
