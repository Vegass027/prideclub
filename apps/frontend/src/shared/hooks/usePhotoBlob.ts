import { useEffect, useState } from "react";
import { apiClient } from "@/shared/api/client";

/** Хук: превращает относительный URL `/api/v1/users/{id}/photo` в blob URL
 *  для использования в `<img src>`. initData подставляется автоматически
 *  через apiClient interceptor.
 *
 *  - relativeUrl=null → blobUrl=null (Avatar fallback на инициалы)
 *  - 401/404/502 → blobUrl=null (Avatar fallback)
 *  - cleanup через URL.revokeObjectURL — без утечек.
 */
export function usePhotoBlob(relativeUrl: string | null): string | null {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!relativeUrl) {
      setBlobUrl(null);
      return;
    }

    let cancelled = false;
    let createdUrl: string | null = null;

    (async () => {
      try {
        const res = await apiClient.get<Blob>(relativeUrl, {
          responseType: "blob",
        });
        if (cancelled) return;
        createdUrl = URL.createObjectURL(res.data);
        setBlobUrl(createdUrl);
      } catch {
        if (cancelled) return;
        setBlobUrl(null);
      }
    })();

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [relativeUrl]);

  return blobUrl;
}
