# Click TV manual channel upload

এই folder-এ `.m3u`, `.m3u8`, `.json` অথবা `.txt` playlist file রাখুন। পরবর্তী `channels`/`all` scan-এ file নিজে load হবে।

```m3u
#EXTM3U
#EXTINF:-1 tvg-id="my-channel" tvg-name="My Channel" tvg-logo="https://example.com/logo.png" group-title="Bangla",My Channel 1080p
#EXTVLCOPT:http-referrer=https://example.com/
https://example.com/live/master.m3u8
```

- Channel quality 720p বা তার বেশি হতে হবে।
- Cookie/Referer/Origin/DRM থাকলে stream entry-এর সঙ্গে দিন।
- Movie entry হলে `group-title="Movie: Hindi"`-এর মতো explicit Movie marker দিন।
- Manual channel-ও actual playback ও 720p rule pass করার পর publish হবে।
- একই URL কিন্তু আলাদা Cookie/header/DRM হলে আলাদা candidate হিসেবে রাখা হবে।
