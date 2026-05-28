"use client";

import { useState } from "react";


type Size = "sm" | "md" | "lg";


const SIZE_CLASSES: Record<Size, string> = {
  sm: "w-10 h-14",   // Home dropdown thumbnails
  md: "w-16 h-24",   // Recommendations cards
  lg: "w-24 h-36",   // Library cards
};


/**
 * Generate a stable hue from a string. Same title -> same color.
 * Used for the gradient fallback when cover_url is missing.
 */
function hashHue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (h << 5) - h + seed.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h) % 360;
}


export function BookCover({
  url,
  title,
  size = "md",
}: {
  url: string | null;
  title: string;
  size?: Size;
}) {
  // `failed` tracks whether the image element raised an onError. Once it has,
  // we permanently render the fallback even if `url` is non-null.
  const [failed, setFailed] = useState(false);
  const showFallback = !url || failed;

  if (showFallback) {
    const hue = hashHue(title);
    return (
      <div
        className={
          SIZE_CLASSES[size] +
          " shrink-0 rounded shadow-sm flex items-center justify-center text-white font-semibold text-xs text-center p-2 leading-tight"
        }
        style={{
          background: `linear-gradient(135deg, hsl(${hue}, 60%, 45%), hsl(${(hue + 40) % 360}, 60%, 35%))`,
        }}
        aria-label={`Cover placeholder for ${title}`}
      >
        {title.length > 22 ? title.slice(0, 20) + "…" : title}
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={`Cover of ${title}`}
      loading="lazy"
      onError={() => setFailed(true)}
      className={SIZE_CLASSES[size] + " shrink-0 rounded shadow-sm object-cover bg-gray-100"}
    />
  );
}
