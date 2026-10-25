(function () {

    const preset = document.getElementById("exportPreset");

    preset.addEventListener("change", function () {

        document.querySelectorAll(".export-custom").forEach(function (el) {
            el.hidden = preset.value !== "custom";
        });

        if (preset.value !== "custom") {
            document.getElementById("exportForm").submit();
        }

    });

})();
