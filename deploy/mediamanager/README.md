# CineMind-fliken i MediaManager

Ligger här tills MediaManagers källträd på NAS:en är rätt kodbas igen (se
CineMind `HANDOFF.md` omgång 7). Lägg då in, över SSH, aldrig via SMB:

1. `web/src/routes/dashboard/cinemind/+page.svelte` → samma sökväg i
   `/vol2/1000/MediaManager/web/src/routes/dashboard/cinemind/`.
2. I `web/src/lib/components/nav/app-sidebar.svelte`: importera `Popcorn` från
   `lucide-svelte` och lägg efter posten "AI Recommendations" i `navMain`:

   ```ts
   {
   	title: 'CineMind',
   	url: resolve('/dashboard/cinemind', {}),
   	icon: Popcorn,
   	isActive: true,
   	adminOnly: false
   },
   ```
3. Bygg MM enligt dess egen CLAUDE.md:
   `cd /vol2/1000/MediaManager && docker compose up -d --build mediamanager`

CineMind-containern (`mediamanager_cinemind`, port 8001) och dess MongoDB
(`mediamanager_cinemind_mongo`, data i `./cinemind-data/mongo`) kör redan och
kräver inget MM-bygge.
