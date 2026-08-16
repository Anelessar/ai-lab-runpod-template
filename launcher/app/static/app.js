const search = document.querySelector("#tool-search");
if (search) {
  search.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    document.querySelectorAll(".tool-card").forEach((card) => {
      card.hidden = query && !card.dataset.search.includes(query);
    });
  });
}

const activeJobs = [...document.querySelectorAll(".job")].some((node) =>
  node.textContent.includes("running") || node.textContent.includes("queued")
);
if (activeJobs) window.setTimeout(() => window.location.reload(), 8000);
