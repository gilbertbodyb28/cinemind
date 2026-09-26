# CineMind-fliken i MediaManager

Referenskopia av CineMinds tillägg i MediaManagers källträd på NAS:en
(`/vol2/1000/MediaManager`, se CineMind `HANDOFF.md` omgång 7 och 11). Om MM-trädet
återställs igen: lägg in allt nedan, över SSH, aldrig via SMB, och verifiera med
`md5sum` på NAS:en.

1. `web/src/routes/dashboard/cinemind/+page.svelte` → samma sökväg i
   `/vol2/1000/MediaManager/web/src/routes/dashboard/cinemind/`.
2. **Posten "CineMind" i alla fyra navigeringarna.** Vilken som visas beror på temat
   och sidomenyläget, som sparas i webbläsaren (`mediamanager.appearance`,
   `mediamanager.sidebar-preferences`), så posten måste finnas i alla. Omgång 7 lade
   bara in den i den klassiska sidomenyn, och den syntes därför inte i Glass 27,
   Liquid Glass eller hover-railen. Ikon `Popcorn` från `lucide-svelte/icons/popcorn`,
   länk `resolve('/dashboard/cinemind', {})`:

   | fil (under `web/src/lib/components/`) | var |
   |---|---|
   | `nav/app-sidebar.svelte` | `navMain`, efter "AI Recommendations" (`title`, `url`, `icon`, `isActive: true`, `adminOnly: false`) |
   | `glass27/glass27-navigation.svelte` | gruppen "System", efter "AI Recommendations" (`label`, `href`, `icon`) |
   | `media-manager-liquid-glass/media-manager-liquid-glass-navigation.svelte` | `dashboardMenuItems` efter Avsnittsscanner, `secondaryShellItems` efter Discover, `qbittorrentShellItems` efter AI Recommendations (`key: 'cinemind'`, `label`, `href`, `icon`) |
   | `nav/chatgpt-app-rail.svelte` | `items`, efter "AI Recommendations" (`title`, `url`, `icon`) |

3. **Sidtiteln** `'/dashboard/cinemind': ['CineMind']` i `exactRouteTrails` i
   `glass27/glass27-header.svelte`, `vision-ui/vision-header.svelte` och
   `liquid-ui/liquid-header.svelte`, som för Avsnittsscanner. Den syns inte än: headern jämför
   med `/dashboard…` medan sökvägen börjar med `/web`, så den säger "Dashboard" på alla
   undersidor (CineMind `HANDOFF.md` omgång 11).
4. Bygg bara MM, så att databasen inte återskapas:
   `cd /vol2/1000/MediaManager && docker compose up -d --build --no-deps mediamanager`

Kontrollera sedan att fliken finns i den byggda frontenden: hämta
`/web/dashboard/cinemind`, läs ut `entry/app.<hash>.js`, och `grep` efter
`dashboard/cinemind` i noderna som bär navigeringarna. Ladda om sidan hårt i
webbläsaren (Cmd+Shift+R) efter ett bygge.

CineMind-containern (`mediamanager_cinemind`, port 8001) och dess MongoDB
(`mediamanager_cinemind_mongo`, data i `./cinemind-data/mongo`) kör redan och
kräver inget MM-bygge.
