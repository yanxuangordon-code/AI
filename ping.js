// ping.js — pre-wakes the Render backend as soon as any page loads.
// Include this in dashboard.html and index.html with:
//   <script src="ping.js"></script>
// It silently pings the backend root so the cold-start happens in the
// background while the user is still looking at the page, not after
// they click something.
(function() {
  var API = "https://codesponge-backend.onrender.com";
  // Fire and forget — we don't care about the response
  fetch(API + "/ping", { method: "GET" }).catch(function() {});
})();
