// READ-ONLY. Makes no requests; reads only the address bar of the current tab.
// 1. On a LinkedIn people search, pick ONE place in the Locations filter and click "Show results".
// 2. Press F12 > Console, paste this, press Enter, and type the place name when asked.
// 3. Send the printed line(s). They go into extension/config.js -> geo.ids.
(() => {
  const ids = JSON.parse(new URL(location.href).searchParams.get('geoUrn') || '[]');
  if (!ids.length) return console.log('No geoUrn in this address. Pick a place in Locations and click Show results first.');
  const place = (prompt('Which place did you pick? (e.g. Hyderabad, Telangana, India)') || '?').trim();
  ids.forEach((id) => console.log(`${place} -> ${id}`));
  if (ids.length > 1) console.log('More than one place is selected: keep only one to know which ID is which.');
})();
