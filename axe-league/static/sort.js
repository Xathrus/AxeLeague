// Click a column header on any stats table to sort by it; click again to
// reverse. Numeric-aware ("60", "87.5%", "12.3"); em-dashes sort last.
(function () {
  document.querySelectorAll("table.stats-table").forEach(function (table) {
    var ths = table.querySelectorAll("thead th");
    ths.forEach(function (th, idx) {
      th.classList.add("sortable");
      th.title = "Sort by " + th.textContent.trim();
      th.addEventListener("click", function () {
        var tbody = table.querySelector("tbody");
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
        var dir = th.dataset.dir === "desc" ? "asc" : "desc"; // first click: best on top
        ths.forEach(function (t) {
          delete t.dataset.dir;
          t.classList.remove("sort-asc", "sort-desc");
        });
        th.dataset.dir = dir;
        th.classList.add(dir === "asc" ? "sort-asc" : "sort-desc");

        function val(td) {
          var t = td ? td.textContent.trim() : "";
          if (t === "" || t === "—") return null;
          var n = parseFloat(t.replace("%", ""));
          return isNaN(n) ? t.toLowerCase() : n;
        }
        rows.sort(function (a, b) {
          var va = val(a.children[idx]);
          var vb = val(b.children[idx]);
          if (va === null && vb === null) return 0;
          if (va === null) return 1;  // blanks always last
          if (vb === null) return -1;
          if (typeof va === "string" || typeof vb === "string") {
            va = String(va); vb = String(vb);
            return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
          }
          return dir === "asc" ? va - vb : vb - va;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  });
})();
