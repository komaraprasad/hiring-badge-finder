/* Hiring Badge Finder - runs INSIDE LinkedIn's people-search page (page world), before LinkedIn's code.
 *
 * LinkedIn's newer results page loads the rest of its results only when its "load more" area becomes
 * visible on screen (an IntersectionObserver). The browser never reports anything visible in a
 * background tab, so a hidden search tab got only the first 2 people (seen on several accounts).
 *
 * While the tab is HIDDEN, every element the page watches is reported as visible, so LinkedIn's own
 * code loads its own results, exactly as when a person scrolls the page. Animation frames are also
 * kept running while hidden (the browser pauses them), because LinkedIn draws the new results in one.
 * When the tab is visible, the browser's normal behaviour is untouched.
 *
 * Nothing is sent, stored or read here; it only changes what the page is told about visibility.
 */
(() => {
  if (window.__hbfVisibility) return;
  window.__hbfVisibility = true;
  const hidden = () => document.visibilityState === 'hidden';

  const Native = window.IntersectionObserver;
  if (typeof Native === 'function') {
    const entryFor = (target) => {
      const r = target.getBoundingClientRect();
      return { target, isIntersecting: true, intersectionRatio: 1, time: performance.now(),
               boundingClientRect: r, intersectionRect: r, rootBounds: null };
    };
    class HbfIntersectionObserver extends Native {
      constructor(callback, options) {
        super(callback, options);
        this.__cb = callback;
        this.__told = new WeakSet();
      }
      observe(target) {
        super.observe(target);
        if (!hidden() || !target) return;
        // Told once per element, a moment later (as the browser would), only while the tab is hidden.
        setTimeout(() => {
          if (!hidden() || this.__told.has(target) || !target.isConnected) return;
          this.__told.add(target);
          try { this.__cb.call(this, [entryFor(target)], this); } catch (e) { /* the page's own error */ }
        }, 50);
      }
      unobserve(target) { if (target) this.__told.delete(target); super.unobserve(target); }
    }
    window.IntersectionObserver = HbfIntersectionObserver;
  }

  const nativeRaf = window.requestAnimationFrame && window.requestAnimationFrame.bind(window);
  const nativeCancel = window.cancelAnimationFrame && window.cancelAnimationFrame.bind(window);
  if (nativeRaf) {
    const timers = new Map();
    let next = 1;
    window.requestAnimationFrame = (cb) => {
      if (!hidden()) return nativeRaf(cb);
      const id = -(next++);                                   // negative: never clashes with the browser's ids
      timers.set(id, setTimeout(() => { timers.delete(id); cb(performance.now()); }, 16));
      return id;
    };
    window.cancelAnimationFrame = (id) => {
      if (id < 0) { clearTimeout(timers.get(id)); timers.delete(id); return; }
      if (nativeCancel) nativeCancel(id);
    };
  }
})();
