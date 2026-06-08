// 주식 AI 분석 - 서비스 워커
// 전략:
//   - data.json: 항상 네트워크 우선, 실패 시 캐시 (최신 데이터 보장 + 오프라인 폴백)
//   - 정적 파일(html/css/svg/manifest): 캐시 우선, 백그라운드에서 갱신
//   - 그 외: 네트워크 우선

const CACHE_VERSION = 'v2026-06-08-zoom';
const STATIC_CACHE  = `stock-static-${CACHE_VERSION}`;
const DATA_CACHE    = `stock-data-${CACHE_VERSION}`;

// 처음 설치 시 미리 캐시할 정적 자원
const PRECACHE_URLS = [
  './',
  './index.html',
  './manifest.json',
  './icon.svg',
];

// 설치
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      return cache.addAll(PRECACHE_URLS).catch((err) => {
        console.warn('Precache 일부 실패:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// 활성화: 옛 캐시 정리
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== DATA_CACHE)
          .map((k) => caches.delete(k))
      );
    }).then(() => self.clients.claim())
  );
});

// 요청 처리
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // GET 요청만 처리 (POST 등은 캐시 안 함)
  if (request.method !== 'GET') return;

  // data.json: 네트워크 우선 (캐시 무효화 파라미터 ?_=… 무시하고 캐시 키 통일)
  if (url.pathname.endsWith('data.json')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // 정적 파일: 캐시 우선
  if (PRECACHE_URLS.some((p) => url.pathname.endsWith(p.replace('./', '')))) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 기타: 네트워크 우선, 실패 시 캐시
  event.respondWith(networkFirst(request));
});

// 네트워크 우선 (실패 시 캐시 폴백)
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(DATA_CACHE);
      // 캐시 키는 쿼리스트링 제거한 URL (?_= 캐시 우회용 파라미터 통일)
      const cacheKey = stripQuery(request.url);
      cache.put(cacheKey, response.clone());
    }
    return response;
  } catch (err) {
    const cacheKey = stripQuery(request.url);
    const cached = await caches.match(cacheKey);
    if (cached) return cached;
    // 마지막 시도: 정적 캐시 확인
    const staticCached = await caches.match(request);
    if (staticCached) return staticCached;
    throw err;
  }
}

// 캐시 우선 (백그라운드 갱신)
async function cacheFirst(request) {
  const cached = await caches.match(request);
  // 백그라운드에서 새 버전 받아두기 (fire and forget)
  fetch(request).then((response) => {
    if (response.ok) {
      caches.open(STATIC_CACHE).then((cache) => cache.put(request, response));
    }
  }).catch(() => {});
  if (cached) return cached;
  // 캐시 없으면 네트워크 응답 그대로
  return fetch(request);
}

function stripQuery(urlStr) {
  const u = new URL(urlStr);
  u.search = '';
  return u.toString();
}
