# Zee Bangla সমস্যার বাংলা রিপোর্ট

এই folder-এ Click TV-এর `Live TV → Indian → ২ নম্বর Zee Bangla` channel-এর আটকে আটকে চলা এবং browser playback failure-এর প্রমাণভিত্তিক বাংলা diagnosis রাখা হয়েছে।

## Download

- [সম্পূর্ণ বাংলা রিপোর্ট](./ZEE_BANGLA_SOMOSSA_O_SOMADHAN_BN.txt)
- [Real browser test screenshot](./REAL_BROWSER_TEST.png)
- [রিপোর্ট ও screenshot-এর ZIP](./ZEE_BANGLA_REPORT_BN.zip)

## এক লাইনের সিদ্ধান্ত

বর্তমান raw MPEG-TS source ছোট finite burst দিয়ে connection বন্ধ করছে, captured video-তে H.264 IDR keyframe নেই, দুই proxy একই origin ব্যবহার করছে এবং কোনো আসল backup নেই। তাই buffer বাড়ানো একা সমস্যাটি সমাধান করবে না; যাচাইকৃত rolling HTTPS HLS primary ও independent backup দরকার।

