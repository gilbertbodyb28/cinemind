import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { Clapperboard } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import Layout from "@/components/Layout";
import Landing from "@/pages/Landing";
import Home from "@/pages/Home";
import Dashboard from "@/pages/Dashboard";
import Recommendations from "@/pages/Recommendations";
import Saved from "@/pages/Saved";
import TasteProfile from "@/pages/TasteProfile";
import Connections from "@/pages/Connections";
import Jobs from "@/pages/Jobs";
import Requests from "@/pages/Requests";
import Approved from "@/pages/Approved";
import Logs from "@/pages/Logs";
import AiSearch from "@/pages/AiSearch";
import Profile from "@/pages/Profile";

function LoadingScreen() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="glass flex items-center gap-4 px-6 py-5 rounded-2xl">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[rgba(216,178,106,0.18)] text-[#EBD3A3]">
          <Clapperboard size={23} />
        </span>
        <div>
          <p className="font-display text-lg font-bold">CineMind AI</p>
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D]">
            Loading your cinema
          </p>
        </div>
      </div>
    </main>
  );
}

function ProtectedRoutes() {
  const { user, loading } = useAuth();
  if (loading) return <LoadingScreen />;
  if (!user) return <Navigate to="/" replace />;
  return (
    <Layout>
      <Outlet />
    </Layout>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route element={<ProtectedRoutes />}>
        <Route path="/dashboard" element={<Home />} />
        <Route path="/stats" element={<Dashboard />} />
        <Route path="/recommendations" element={<Recommendations />} />
        <Route path="/saved" element={<Saved />} />
        <Route path="/taste" element={<TasteProfile />} />
        <Route path="/connections" element={<Connections />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/requests" element={<Requests />} />
        <Route path="/approved" element={<Approved />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/search" element={<AiSearch />} />
        <Route path="/profile" element={<Profile />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
