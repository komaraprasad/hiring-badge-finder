// READ-ONLY. Run AFTER you opened the Locations filter on a LinkedIn people-search page
// and typed a city (for example "Bengaluru") so the suggestions appeared. Do not click Apply.
// It lists the web addresses the page used; it makes no requests of its own.
copy(JSON.stringify(
  performance.getEntriesByType('resource').map(e => e.name)
    .filter(n => /typeahead|geo|location/i.test(n)).slice(-15), null, 1));
console.log('copied');
