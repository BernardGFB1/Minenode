// Friendly editor schema for server.properties (keys not listed here remain editable in the raw table).
export const GROUPS = [
  {
    id: 'general', title: 'Ogólne', icon: 'Sparkles',
    fields: [
      { key: 'motd', label: 'MOTD (opis na liście serwerów)', type: 'motd', default: 'A Minecraft Server', wide: true, hint: 'Kody kolorów: §a zielony, §c czerwony, §l pogrubienie, §r reset. Nowa linia: \\n' },
      { key: 'max-players', label: 'Maks. liczba graczy', type: 'number', default: '20', min: 1, max: 1000 },
      { key: 'difficulty', label: 'Poziom trudności', type: 'select', default: 'easy', options: [['peaceful', 'Pokojowy'], ['easy', 'Łatwy'], ['normal', 'Normalny'], ['hard', 'Trudny']] },
      { key: 'gamemode', label: 'Domyślny tryb gry', type: 'select', default: 'survival', options: [['survival', 'Przetrwanie'], ['creative', 'Kreatywny'], ['adventure', 'Przygoda'], ['spectator', 'Obserwator']] },
      { key: 'hardcore', label: 'Hardcore', type: 'bool', default: 'false', hint: 'Śmierć = tryb obserwatora' },
      { key: 'pvp', label: 'PvP', type: 'bool', default: 'true', hint: 'Walka między graczami' },
      { key: 'force-gamemode', label: 'Wymuszaj tryb gry', type: 'bool', default: 'false', hint: 'Przy każdym wejściu' },
      { key: 'allow-flight', label: 'Zezwalaj na latanie', type: 'bool', default: 'false', hint: 'Wymagane przez część modów' },
    ],
  },
  {
    id: 'world', title: 'Świat', icon: 'Globe2',
    fields: [
      { key: 'level-name', label: 'Nazwa folderu świata', type: 'text', default: 'world' },
      { key: 'level-seed', label: 'Seed', type: 'text', default: '', placeholder: 'losowy', hint: 'Działa tylko przy generowaniu nowego świata' },
      { key: 'level-type', label: 'Typ świata', type: 'select', default: 'minecraft:normal', options: [['minecraft:normal', 'Normalny'], ['minecraft:flat', 'Płaski'], ['minecraft:large_biomes', 'Duże biomy'], ['minecraft:amplified', 'Wzmocniony'], ['minecraft:single_biome_surface', 'Jeden biom']] },
      { key: 'view-distance', label: 'Zasięg widzenia (chunki)', type: 'range', default: '10', min: 2, max: 32, hint: 'Duży wpływ na wydajność' },
      { key: 'simulation-distance', label: 'Zasięg symulacji (chunki)', type: 'range', default: '10', min: 2, max: 32 },
      { key: 'spawn-protection', label: 'Ochrona spawnu (bloki)', type: 'number', default: '16', min: 0, max: 1000 },
      { key: 'max-world-size', label: 'Maks. promień świata', type: 'number', default: '29999984', min: 1, max: 29999984 },
      { key: 'generate-structures', label: 'Generuj struktury', type: 'bool', default: 'true', hint: 'Wioski, świątynie…' },
      { key: 'allow-nether', label: 'Nether', type: 'bool', default: 'true' },
      { key: 'spawn-monsters', label: 'Potwory', type: 'bool', default: 'true' },
      { key: 'spawn-animals', label: 'Zwierzęta', type: 'bool', default: 'true' },
      { key: 'spawn-npcs', label: 'Wieśniacy', type: 'bool', default: 'true' },
    ],
  },
  {
    id: 'players', title: 'Gracze i bezpieczeństwo', icon: 'ShieldCheck',
    fields: [
      { key: 'online-mode', label: 'Tryb online (weryfikacja kont)', type: 'bool', default: 'true', hint: 'Wyłącz tylko dla serwerów offline / za BungeeCord' },
      { key: 'white-list', label: 'Whitelista', type: 'bool', default: 'false' },
      { key: 'enforce-whitelist', label: 'Wymuszaj whitelistę', type: 'bool', default: 'false' },
      { key: 'enforce-secure-profile', label: 'Wymagaj podpisanego czatu', type: 'bool', default: 'true' },
      { key: 'op-permission-level', label: 'Domyślny poziom OP', type: 'select', default: '4', options: [['1', '1 – spawn'], ['2', '2 – komendy'], ['3', '3 – moderator'], ['4', '4 – administrator']] },
      { key: 'player-idle-timeout', label: 'Wyrzucaj AFK po (min)', type: 'number', default: '0', min: 0, max: 1440, hint: '0 = nigdy' },
      { key: 'enable-command-block', label: 'Bloki poleceń', type: 'bool', default: 'false' },
      { key: 'hide-online-players', label: 'Ukryj listę graczy', type: 'bool', default: 'false', hint: 'W pingu serwera' },
      { key: 'prevent-proxy-connections', label: 'Blokuj połączenia przez VPN', type: 'bool', default: 'false' },
    ],
  },
  {
    id: 'network', title: 'Sieć i wydajność', icon: 'Gauge',
    fields: [
      { key: 'network-compression-threshold', label: 'Próg kompresji (bajty)', type: 'number', default: '256', min: -1, max: 65535, hint: '-1 wyłącza kompresję' },
      { key: 'rate-limit', label: 'Limit pakietów/s', type: 'number', default: '0', min: 0, max: 100000, hint: '0 = brak limitu' },
      { key: 'max-tick-time', label: 'Watchdog (ms)', type: 'number', default: '60000', min: -1, max: 600000, hint: '-1 wyłącza watchdog' },
      { key: 'sync-chunk-writes', label: 'Synchroniczny zapis chunków', type: 'bool', default: 'true' },
      { key: 'enable-status', label: 'Widoczny na liście serwerów', type: 'bool', default: 'true' },
      { key: 'enable-query', label: 'Protokół GameSpy Query', type: 'bool', default: 'false' },
      { key: 'enable-rcon', label: 'RCON', type: 'bool', default: 'false', hint: 'Panel nie wymaga RCON' },
    ],
  },
  {
    id: 'resources', title: 'Paczka zasobów', icon: 'Image',
    fields: [
      { key: 'resource-pack', label: 'URL paczki zasobów', type: 'text', default: '', wide: true, placeholder: 'https://…/pack.zip' },
      { key: 'resource-pack-sha1', label: 'SHA-1 paczki', type: 'text', default: '', mono: true },
      { key: 'require-resource-pack', label: 'Wymagaj paczki', type: 'bool', default: 'false' },
      { key: 'resource-pack-prompt', label: 'Komunikat przy pobieraniu', type: 'text', default: '' },
    ],
  },
]
export const KNOWN_KEYS = new Set(GROUPS.flatMap((g) => g.fields.map((f) => f.key)))
