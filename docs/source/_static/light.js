// The site is light only, like www.leithresearch.org. Furo picks the theme
// from this key as the body starts, and this file loads after that, so set
// the current page's theme too.
try {
  localStorage.setItem("theme", "light");
} catch (e) {
  /* storage blocked: the dark palette is the light one anyway */
}
if (document.body) document.body.dataset.theme = "light";
else document.addEventListener("DOMContentLoaded", () => (document.body.dataset.theme = "light"));
