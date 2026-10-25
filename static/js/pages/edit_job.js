        (function () {

            const category = document.getElementById("costCategory");
            const amount = document.getElementById("costAmount");
            const hours = document.getElementById("costHours");
            const rate = document.getElementById("costRate");
            const total = document.getElementById("costTotal");

            function refresh() {

                const isWork = category.value === "praca";

                document.querySelectorAll('#costForm [data-for="praca"]').forEach(function (el) { el.hidden = !isWork; });
                document.querySelector('#costForm [data-for="amount"]').hidden = isWork;

                amount.required = !isWork;
                hours.required = isWork;
                rate.required = isWork;

                const value = (parseFloat(hours.value) || 0) * (parseFloat(rate.value) || 0);

                total.textContent = value.toFixed(2).replace(".", ",") + " €";

            }

            [category, hours, rate].forEach(function (el) { el.addEventListener("input", refresh); });

            refresh();

        })();

        
