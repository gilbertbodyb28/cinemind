import { useEffect, useState } from "react";
import { Play, Clapperboard, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

export default function TrailerPlayer({ rec }) {
  const [trailer, setTrailer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    setLoading(true); setPlaying(false); setTrailer(null);
    api.get(`/recommendations/${rec.id}/trailer`)
      .then((r) => setTrailer(r.data))
      .catch(() => setTrailer({ key: null }))
      .finally(() => setLoading(false));
  }, [rec.id]);

  if (loading) {
    return (
      <div data-testid="trailer-loading" className="mt-6 glass rounded-2xl h-14 flex items-center gap-2 px-5 text-xs font-mono uppercase tracking-widest text-slate-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Finding trailer
      </div>
    );
  }
  if (!trailer?.key) {
    return (
      <div data-testid="trailer-unavailable" className="mt-6 glass rounded-2xl h-14 flex items-center gap-2 px-5 text-xs font-mono uppercase tracking-widest text-slate-500">
        <Clapperboard className="w-3.5 h-3.5" /> No trailer available
      </div>
    );
  }

  return (
    <div data-testid="trailer-player" className="mt-6">
      <div className="flex items-center gap-2 mb-3">
        <Clapperboard className="w-4 h-4 text-amber-300" />
        <span className="font-mono text-xs uppercase tracking-widest text-slate-400">Trailer</span>
        {trailer.name && <span className="text-xs text-slate-500 truncate">· {trailer.name}</span>}
      </div>
      <div className="relative aspect-video rounded-2xl overflow-hidden border border-white/10 bg-black">
        {playing ? (
          <iframe
            data-testid="trailer-iframe"
            className="absolute inset-0 w-full h-full"
            src={`https://www.youtube-nocookie.com/embed/${trailer.key}?autoplay=1&rel=0&modestbranding=1`}
            title={`${rec.title} trailer`}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          />
        ) : (
          <button data-testid="trailer-play-button" onClick={() => setPlaying(true)} className="group absolute inset-0 w-full h-full">
            <img src={`https://img.youtube.com/vi/${trailer.key}/hqdefault.jpg`} alt="" className="w-full h-full object-cover opacity-80 group-hover:opacity-100 transition-opacity" />
            <div className="absolute inset-0 grid place-items-center">
              <span className="w-16 h-16 rounded-full glass-strong grid place-items-center group-hover:brutal-shadow-rose transition-shadow">
                <Play className="w-6 h-6 fill-current text-white ml-0.5" />
              </span>
            </div>
          </button>
        )}
      </div>
    </div>
  );
}
