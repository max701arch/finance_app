(function () {
    "use strict";

    var themeStorageKey = "finance_theme";

    function parseDashboardData() {
        var node = document.getElementById("dashboard-data");
        if (node) {
            try {
                return JSON.parse(node.textContent || "{}");
            } catch (_err) {
                return {};
            }
        }

        if (window.dashboardData && typeof window.dashboardData === "object") {
            return window.dashboardData;
        }

        return {};
    }

    var data = parseDashboardData();

    function getStoredTheme() {
        try {
            return localStorage.getItem(themeStorageKey);
        } catch (_err) {
            return null;
        }
    }

    function preferredTheme() {
        var saved = getStoredTheme();
        if (saved === "dark" || saved === "light") {
            return saved;
        }

        if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
            return "dark";
        }

        return "light";
    }

    function setTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);

        var toggle = document.getElementById("themeToggle");
        if (toggle) {
            toggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
        }

        window.dispatchEvent(new Event("resize"));
    }

    function initTheme() {
        var theme = preferredTheme();
        setTheme(theme);

        var toggle = document.getElementById("themeToggle");
        if (!toggle) {
            return;
        }

        toggle.addEventListener("click", function () {
            var current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
            var next = current === "dark" ? "light" : "dark";

            try {
                localStorage.setItem(themeStorageKey, next);
            } catch (_err) {
                // localStorage unavailable, ignore and only switch current session theme.
            }

            setTheme(next);
        });
    }

    function initConfirmForms() {
        var forms = document.querySelectorAll("form[data-confirm]");
        forms.forEach(function (form) {
            form.addEventListener("submit", function (event) {
                var message = form.getAttribute("data-confirm");
                if (message && !window.confirm(message)) {
                    event.preventDefault();
                }
            });
        });
    }

    function initTrendChart() {
        var canvas = document.getElementById("trendChart");
        var labels = Array.isArray(data.months) ? data.months : [];
        var income = Array.isArray(data.income_data) ? data.income_data : [];
        var expense = Array.isArray(data.expense_data) ? data.expense_data : [];

        if (!canvas || typeof window.Chart !== "function") {
            return;
        }

        new window.Chart(canvas, {
            type: "bar",
            data: {
                labels: labels,
                datasets: [
                    {
                        label: "Income",
                        data: income,
                        backgroundColor: "rgba(21, 128, 61, 0.78)",
                    },
                    {
                        label: "Expense",
                        data: expense,
                        backgroundColor: "rgba(194, 65, 12, 0.74)",
                    },
                ],
            },
        });
    }

    function initCategoryChart() {
        var canvas = document.getElementById("categoryChart");
        var labels = Array.isArray(data.categories) ? data.categories : [];
        var values = Array.isArray(data.category_data) ? data.category_data : [];

        if (!canvas || typeof window.Chart !== "function") {
            return;
        }

        var palette = ["#0f766e", "#c2410c", "#2563eb", "#0e7490", "#eab308", "#334155", "#15803d"];
        var colors = labels.map(function (_, idx) {
            return palette[idx % palette.length];
        });

        new window.Chart(canvas, {
            type: "doughnut",
            data: {
                labels: labels,
                datasets: [
                    {
                        data: values,
                        backgroundColor: colors,
                    },
                ],
            },
        });
    }

    function init() {
        initTheme();
        initConfirmForms();
        initTrendChart();
        initCategoryChart();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();