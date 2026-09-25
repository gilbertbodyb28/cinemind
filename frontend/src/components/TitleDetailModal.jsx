import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Clapperboard, Loader2, Play, Star, Users, X } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Double-click any poster in the app to open this: the YouTube trailer, who is in it
 * and what it is about. Details come from TMDb through the backend, which
 * caches them for a day, so reopening a title is instant.
 */
export default function TitleDetailModal({ item, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!item) return undefined;
    let alive = true;
    setLoading(true);
    setPlaying(false);
    setData(null);
    // Queued titles have their own row; any other poster is found by title.
    const request = item.generic
      ? api.get("/titles/details", {
          params: { title: item.title, year: item.year, type: item.type, tmdb_id: item.tmdb_id },
        })
      : api.get(`/requests/${item.id}/details`);
    request
      .then((r) => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setData({ overview: null, cast: [], trailer: null }); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [item]);

  useEffect(() => {
    if (!item) return undefined;
    const onKey = (event) => { if (event.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [item, onClose]);

  if (!item) return null;

  const trailerKey = data?.trailer?.key;
  const genres = data?.genres?.length ? data.genres : item.genres || [];
  const facts = [
    data?.release_date || item.year,
    data?.seasons ? `${data.seasons} season${data.seasons === 1 ? "" : "s"}` : null,
    data?.episodes ? `${data.episodes} episodes` : null,
    data?.runtime ? `${data.runtime} min` : null,
    data?.status,
  ].filter(Boolean);

  return createPortal(
    <div
      data-testid="title-detail-modal"
      className="fixed inset-0 z-[70] p-2 sm:p-4 bg-black/75 backdrop-blur-sm"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose?.(); }}
    >
      <div className="glass-shell rounded-[26px] w-full h-full overflow-y-auto scroll-thin grain">
        <div className="relative h-48 sm:h-72 lg:h-80 overflow-hidden">
          {item.backdrop || data?.backdrop || item.poster ? (
            <img src={item.backdrop || data?.backdrop || item.poster} alt="" className="w-full h-full object-cover" />
          ) : null}
          <div className="absolute inset-0 bg-gradient-to-t from-[#17130F] via-[#17130F]/45 to-transparent" />
          <button
            type="button"
            data-testid="title-detail-close"
            onClick={onClose}
            aria-label="Close"
            className="absolute top-4 right-4 w-9 h-9 rounded-full glass grid place-items-center text-[#BFB09A] hover:text-[#F6EFE4] transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 sm:px-10 pb-10 -mt-14 relative">
          <div className="flex gap-4 sm:gap-6">
            {item.poster && (
              <span className="poster-frame block w-24 sm:w-40 shrink-0">
              <img
                src={item.poster}
                alt={item.title}
                className="w-full aspect-[2/3] object-cover"
              />
              </span>
            )}
            <div className="min-w-0 flex-1 pt-14">
              <h2 data-testid="title-detail-heading" className="font-display text-2xl sm:text-3xl font-extrabold leading-tight">
                {data?.title || item.title}
              </h2>
              {data?.tagline && <p className="text-sm text-[#BFB09A] italic mt-1">{data.tagline}</p>}
              <div className="flex flex-wrap items-center gap-1.5 mt-3">
                {item.type && <span className="chip">{item.type}</span>}
                {data?.rating != null && (
                  <span className="chip chip-amber flex items-center gap-1">
                    <Star className="w-3 h-3 fill-current" /> {Number(data.rating).toFixed(1)}
                  </span>
                )}
                {facts.map((fact) => <span key={String(fact)} className="chip">{fact}</span>)}
              </div>
              {genres.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {genres.slice(0, 6).map((g) => <span key={g} className="chip chip-cyan">{g}</span>)}
                </div>
              )}
            </div>
          </div>

          <section className="mt-6">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D] mb-2">About</h3>
            {loading ? (
              <p className="text-sm text-[#8C7F6D] flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading details</p>
            ) : (
              <p data-testid="title-detail-overview" className="text-sm text-[#DCCFBC] leading-relaxed">
                {data?.overview || "No description available for this title."}
              </p>
            )}
          </section>

          <section className="mt-6">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D] mb-2 flex items-center gap-2">
              <Clapperboard className="w-3.5 h-3.5 text-[#D8B26A]" /> Trailer
              {data?.trailer_scope === "series" && <span className="chip">Series trailer</span>}
            </h3>
            {loading ? (
              <div className="glass rounded-2xl h-14 flex items-center gap-2 px-5 text-xs font-mono uppercase tracking-widest text-[#8C7F6D]">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Finding trailer
              </div>
            ) : !trailerKey ? (
              <div data-testid="title-detail-no-trailer" className="glass rounded-2xl h-14 flex items-center gap-2 px-5 text-xs font-mono uppercase tracking-widest text-[#8C7F6D]">
                <Clapperboard className="w-3.5 h-3.5" /> No trailer on YouTube
              </div>
            ) : playing ? (
              <div className="mx-auto max-w-[calc(72vh*16/9)] relative aspect-video rounded-2xl overflow-hidden border border-white/10 bg-black">
                <iframe
                  data-testid="title-detail-trailer-frame"
                  src={`https://www.youtube-nocookie.com/embed/${trailerKey}?autoplay=1&rel=0`}
                  title={data?.trailer?.name || "Trailer"}
                  allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  allowFullScreen
                  className="absolute inset-0 w-full h-full"
                />
              </div>
            ) : (
              <button
                type="button"
                data-testid="title-detail-play-trailer"
                onClick={() => setPlaying(true)}
                className="mx-auto max-w-[calc(72vh*16/9)] relative w-full aspect-video rounded-2xl overflow-hidden border border-white/10 group"
              >
                <img
                  src={`https://img.youtube.com/vi/${trailerKey}/hqdefault.jpg`}
                  alt=""
                  className="absolute inset-0 w-full h-full object-cover opacity-80 group-hover:opacity-100 transition-opacity"
                />
                <span className="absolute inset-0 grid place-items-center">
                  <span className="w-16 h-16 rounded-full glass-strong grid place-items-center group-hover:brutal-shadow-rose transition-shadow">
                    <Play className="w-6 h-6 fill-current ml-1" />
                  </span>
                </span>
              </button>
            )}
          </section>

          <section className="mt-6">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D] mb-3 flex items-center gap-2">
              <Users className="w-3.5 h-3.5 text-[#D8B26A]" /> Cast
            </h3>
            {loading ? (
              <p className="text-sm text-[#8C7F6D] flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading cast</p>
            ) : !data?.cast?.length ? (
              <p data-testid="title-detail-no-cast" className="text-sm text-[#8C7F6D]">No cast listed for this title.</p>
            ) : (
              <div data-testid="title-detail-cast" className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-8 xl:grid-cols-12 gap-3">
                {data.cast.map((person) => (
                  <div key={`${person.name}-${person.character}`} className="min-w-0 text-center">
                    {person.profile ? (
                      <img
                        src={person.profile}
                        alt={person.name}
                        loading="lazy"
                        className="w-full aspect-[2/3] object-cover rounded-xl border border-white/10"
                      />
                    ) : (
                      <div className="w-full aspect-[2/3] rounded-xl border border-white/10 bg-white/[0.04] grid place-items-center text-[#8C7F6D] text-xs">
                        {person.name?.[0] || "?"}
                      </div>
                    )}
                    <div className="text-xs font-medium mt-1.5 truncate">{person.name}</div>
                    {person.character && <div className="text-[10px] text-[#8C7F6D] truncate">{person.character}</div>}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>,
    document.body,
  );
}
