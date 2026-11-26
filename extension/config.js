/*
 * ONE place for everything that depends on LinkedIn's page markup.
 * When LinkedIn changes its page, edit ONLY this file.
 *
 * STATUS: verified = true (Nov 2026, from one real signed-in people-search page,
 * narrow 734px layout; the desktop layout was not captured).
 * Verified on that page:
 *   - each person is a [role="listitem"] inside [role="list"], 10 per page
 *   - framed photo: `figure img` src contains "profile-framedphoto"
 *     (unframed: "profile-displayphoto"); img alt is empty
 *   - "See open roles" is a link to .../opportunities/hiring-opportunities/
 *   - Next is button[data-testid="pagination-controls-next-button-visible"]
 * NOT specific: "profile-framedphoto" means the photo has A frame; a green
 * open-to-work style frame had the same src. Hence badge.requireHiringLink below.
 */
(function (root) {
  root.HBF_CONFIG = {
    verified: true,

    // Links to a person. The FIRST one in a card is the card's person; later ones
    // are mutual connections or that person's "See open roles" page.
    profileLink: 'a[href*="/in/"]',

    // One element per person (real page: role=listitem; hashed classes are not used).
    // Set to '' to fall back to climbing up from each /in/ link.
    cardSelector: '[role="listitem"]',
    // LinkedIn also serves an OLDER results layout (seen Nov 2026 for some searches: only a
    // "People" chip in the filter bar, no role="listitem"). Used only when cardSelector finds
    // no cards. Each item must hold a person link and an image. Structure NOT yet captured.
    fallbackCardSelectors: ['main ul > li', 'main ol > li'],

    // The person's own photo: the first match in the card (mutual-connection
    // avatars come after it).
    photo: 'figure img',

    // Field selectors, tried in order inside a card. First non-empty text wins.
    // If none match, the card's text lines are used (name, degree, headline, location).
    name: [],
    headline: [],
    location: [],
    degree: [],
    // The card's own "Current: Title at Company" line (LinkedIn shows it on many cards).
    // Used by the server only when the headline names no company.
    currentLineRegex: '^current\\s*:\\s*(.+)$',

    // --- Badge markers (strongest signal) -------------------------------
    // Checked on the person's photo only (see `photo`), so a framed
    // mutual-connection avatar does not count.
    // A framed photo alone is NOT a badge: other frames (e.g. a green open-to-work
    // ring) use the same src. Badge = framed photo AND a hiring-opportunities link
    // (openRolesHrefRegex) in the same card. Seen: 9 hiring cards had the link,
    // 1 other-frame card did not (one example only).
    badge: {
      requireHiringLink: true,
      // image src of a framed photo (verified)
      selectors: ['figure img[src*="profile-framedphoto"]'],
      classOrSrcRegex: 'profile-framedphoto',
      // text in alt / aria-label / title (real alt is empty; kept as a backup)
      attrTextRegex: '#\\s*hiring',
      attrs: ['alt', 'aria-label', 'title'],
    },

    // "See open roles": link text, or the link address (verified)
    openRolesRegex: 'see\\s+open\\s+roles',
    openRolesHrefRegex: '/opportunities/hiring-opportunities/',

    // Next button, tried in order
    next: {
      selectors: [
        'button[aria-label="Next"]',
        'button.artdeco-pagination__button--next',
        'a[aria-label="Next"]',
        'button[data-testid="pagination-controls-next-button-visible"]',
      ],
      textRegex: '^next$',
    },

    // --- Page states ----------------------------------------------------
    states: {
      noResultsRegex: 'no results found',
      commercialLimitRegex: 'commercial use limit',
      // LinkedIn asking for a check, or limiting the account
      limitRegex: 'unusual activity|verify you.?re (a )?human|security verification|quick security check|too many requests|temporarily restricted|reached the (weekly|monthly) limit',
      signInPathRegex: '^/(login|authwall|uas/|checkpoint/lg)',
      checkpointPathRegex: '^/checkpoint/',
    },

    // --- Pacing (also enforced by the server) ----------------------------
    // "Cautious" fast mode (chosen Nov 2026, goal: a run in about a minute): a random 1-2.5 s pause
    // before each results page instead of 4-8 s. Daily caps (server) are unchanged.
    pacing: { minWaitMs: 1000, maxWaitMs: 2500, maxPages: 30,
              fullPage: 8,       // with no Next button found, a page with at least this many people goes on to the next page
              detailTabs: 2 },   // classic company-details tabs: only used when fast details cannot read

    // --- Fast lead details (1.46.0) ----------------------------------------
    // Company, Industry and Size from LinkedIn's own profile data (one request per lead, ~0.5 s),
    // the same data its profile page loads, instead of two full pages (Experience + About).
    // Checked on a real signed-in account (Nov 2026, discovery/probe_fast_mode_2.js): Position
    // (companyName, companyUrn, dateRange without end = current), Company (name, url,
    // employeeCountRange, industryUrns), Industry (name). 2 at a time, random 1-2.5 s pause each.
    fastDetails: {
      on: true,
      workers: 2,
      minWaitMs: 1000, maxWaitMs: 2500,
      profileUrl: '/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity={slug}' +
        '&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93',
      // a plain LinkedIn address the hidden helper tab opens (small, no LinkedIn app) to make the requests from
      homeUrl: 'https://www.linkedin.com/robots.txt',
    },

    // --- Rendering all cards on a page -----------------------------------
    // patientChecks / extraTries: when fewer cards are drawn than the page should have, keep scrolling
    // (up to about 10 s more) before reading, instead of stopping after 1 s without new cards.
    // (Short: when the tab still draws too few, the results are read from LinkedIn's page data instead.)
    render: { zoom: '0.25', scrollTries: 12, scrollGapMs: 500, extraTries: 6, patientChecks: 4 },

    // A page that still has fewer people than it should after reading in the background is shown on screen
    // for a few seconds (some accounts' LinkedIn pages load the rest only while they are on screen).
    // Free accounts: with LinkedIn's Premium "Actively hiring" filter, a first page of at most this many people
    // means LinkedIn cut the results off: the search is run again without the filter.
    freeAccount: { maxCards: 3 },

    // LinkedIn's AI-powered people search: top matches first and a "More" control instead of pages.
    // Clicked like a person would (same pause as between pages), at most maxClicks times per search.
    loadMore: { textRegex: '^(show |see |load )?more( results| people)?$', maxClicks: 30, waitMs: 8000 },

    // assumeHidden: tests only (their headless browser reports every tab as visible).
    visibleRead: { on: true, waitVisibleMs: 4000, assumeHidden: false },

    // --- Location IDs (geoUrn) --------------------------------------------
    // Built-in table. Every entry carries a "source:" comment (a test checks this). A city key
    // matches a place with no country, or one in India. Any other place: LinkedIn's location lookup
    // from the set-up tab, then its Locations filter, then the person (see autoSetUp).
    geo: {
      enabled: true,
      ids: {
        'bengaluru, karnataka, india': '105214831', // source: captured from a LinkedIn people-search URL by the user, Nov 2026
        'bangalore, karnataka, india': '105214831', // source: same capture (Bangalore = Bengaluru)
        'bengaluru, india': '105214831',            // source: same capture
        'bangalore, india': '105214831',            // source: same capture
        'bengaluru': '105214831',                   // source: same capture
        'bangalore': '105214831',                   // source: same capture
        'greater bengaluru area': '90009633', // source: table supplied by the user 2026-11-06, not captured here
        'mumbai': '106164952', // source: table supplied by the user 2026-11-06, not captured here
        'mumbai metropolitan region': '90009639', // source: table supplied by the user 2026-11-06, not captured here
        'hyderabad': '105556991', // source: table supplied by the user 2026-11-06, not captured here
        'greater hyderabad area': '90009650', // source: table supplied by the user 2026-11-06, not captured here
        'chennai': '106888327', // source: table supplied by the user 2026-11-06, not captured here
        'delhi': '106187582', // source: table supplied by the user 2026-11-06, not captured here
        'new delhi': '115918471', // source: table supplied by the user 2026-11-06, not captured here
        'delhi ncr': '90009669', // source: table supplied by the user 2026-11-06, not captured here
        'gurugram': '115884833', // source: table supplied by the user 2026-11-06, not captured here
        'noida': '104869687', // source: table supplied by the user 2026-11-06, not captured here
        'pune': '114806696', // source: table supplied by the user 2026-11-06, not captured here
        'kolkata': '111795395', // source: table supplied by the user 2026-11-06, not captured here
        'ahmedabad': '104990346', // source: list supplied by the user 2026-11-06, not captured here
        'hyderabad, telangana, india': '105556991', // source: list supplied by the user 2026-11-06, not captured here
        'chennai, tamil nadu, india': '106888327', // source: list supplied by the user 2026-11-06, not captured here
        'pune, maharashtra, india': '114806696', // source: list supplied by the user 2026-11-06, not captured here
        'delhi, india': '106187582', // source: list supplied by the user 2026-11-06, not captured here
        'new delhi, delhi, india': '115918471', // source: list supplied by the user 2026-11-06, not captured here
        'gurugram, haryana, india': '115884833', // source: list supplied by the user 2026-11-06, not captured here
        'noida, uttar pradesh, india': '104869687', // source: list supplied by the user 2026-11-06, not captured here
        'kolkata, west bengal, india': '111795395', // source: list supplied by the user 2026-11-06, not captured here
        'ahmedabad, gujarat, india': '104990346', // source: list supplied by the user 2026-11-06, not captured here
        'jaipur, rajasthan, india': '100446356', // source: list supplied by the user 2026-11-06, not captured here
        'nagpur, maharashtra, india': '103504847', // source: list supplied by the user 2026-11-06, not captured here
        'kochi, kerala, india': '101034444', // source: list supplied by the user 2026-11-06, not captured here
        'coimbatore, tamil nadu, india': '106346283', // source: list supplied by the user 2026-11-06, not captured here
        'indore, madhya pradesh, india': '104832560', // source: list supplied by the user 2026-11-06, not captured here
        'dubai, united arab emirates': '104305776', // source: list supplied by the user 2026-11-06, not captured here
        'dubai': '104305776', // source: list supplied by the user 2026-11-06, not captured here
        'singapore, singapore': '102454443', // source: list supplied by the user 2026-11-06, not captured here
        'singapore': '102454443', // source: list supplied by the user 2026-11-06, not captured here
        'london, england, united kingdom': '102257491', // source: list supplied by the user 2026-11-06, not captured here
        'london': '102257491', // source: list supplied by the user 2026-11-06, not captured here
        'san francisco bay area': '90000084', // source: list supplied by the user 2026-11-06, not captured here
        'new york, united states': '102571732', // source: list supplied by the user 2026-11-06, not captured here
        'new york': '102571732', // source: list supplied by the user 2026-11-06, not captured here
        'jaipur': '100446356', // source: list supplied by the user 2026-11-06, not captured here
        'nagpur': '103504847', // source: list supplied by the user 2026-11-06, not captured here
        'kochi': '101034444', // source: list supplied by the user 2026-11-06, not captured here
        'coimbatore': '106346283', // source: list supplied by the user 2026-11-06, not captured here
        'indore': '104832560', // source: list supplied by the user 2026-11-06, not captured here
      },
    },

    // --- Company details (industry, size) -------------------------------
    // NOT verified on a real LinkedIn page yet: written from LinkedIn's visible labels.
    // Company search results: links to /company/<id>/ whose text is the company name.
    // About page: the label "Industry" or "Company size" followed by its value.
    company: {
      linkPathRegex: '^/company/([^/?#]+)',
      industryLabels: ['industry'],
      sizeLabels: ['company size'],
      sizeRegex: '^[\\d,.\\s+\\-–]+K?\\s*employees?$',
      // dropped from both names before comparing: "Acme Pvt Ltd" = "Acme"
      legalSuffixRegex: '\\b(pvt|private|ltd|limited|inc|llc|llp|corp|corporation|co|plc|gmbh)\\b',
      contentMs: 20000,
      // Profile (NOT verified on a real page yet): the top card's "Current company: X. Click to
      // skip to experience card" button, and the first entry of the Experience section when its
      // dates say "Present". Its link to /company/<id>/ is the exact company page.
      currentCompanyLabelRegex: '^current company:\\s*(.+?)\\.?(?:\\s+click to skip.*)?$',
      experienceHeading: 'experience',
      presentRegex: '\\bpresent\\b',
      employmentTypeRegex: '^(full-time|part-time|contract|contractual|internship|freelance|self-employed|permanent|apprenticeship|seasonal|trainee)\\b',
      durationRegex: '^\\d+\\s*(yrs?|mos?|years?|months?)\\b',
    },

    // Timeouts (every wait has one, so nothing hangs)
    timeouts: { messageMs: 20000, cardsMs: 20000, pageChangeMs: 20000 },
  };

  /** Markers and rules sent by the dashboard server (GET /api/extension/config) replace the values above,
   *  so a LinkedIn change is fixed on the server and reaches every user at their next search, with no
   *  new download. DATA only (text, numbers, true/false, lists): nothing in it is ever run as code.
   *  Changed in place, so every part of the extension that holds HBF_CONFIG sees the new values. */
  root.HBF_APPLY_OVERRIDES = function apply(over, target) {
    target = target || root.HBF_CONFIG;
    if (!over || typeof over !== 'object' || Array.isArray(over)) return;
    for (const [k, v] of Object.entries(over)) {
      if (k === '__proto__' || k === 'constructor' || k === 'prototype') continue;
      const ok = (x) => x === null || ['string', 'number', 'boolean'].includes(typeof x) ||
        (Array.isArray(x) && x.every((y) => ['string', 'number', 'boolean'].includes(typeof y)));
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        if (!target[k] || typeof target[k] !== 'object' || Array.isArray(target[k])) target[k] = {};
        apply(v, target[k]);
      } else if (ok(v)) {
        target[k] = v;
      }
    }
  };
})(typeof self !== 'undefined' ? self : window);
