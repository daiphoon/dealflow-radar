"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export function RequestStatusRefresher({ intervalMs }: { intervalMs: number | null }) {
  const router = useRouter();

  useEffect(() => {
    if (intervalMs === null) return;
    const refresh = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    const timer = window.setInterval(refresh, intervalMs);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [intervalMs, router]);

  return null;
}
