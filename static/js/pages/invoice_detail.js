function submitStatusChange(select) {

    const form = select.form;

    if (select.value === "Stornovaná") {

        const reason = (window.prompt(
            "Dôvod stornovania faktúry (povinné, zapíše sa do histórie zmien):"
        ) || "").trim();

        if (reason.length < 3) {

            // bez dôvodu sa stav nezmení
            select.value = select.dataset.current;
            return;
        }

        form.elements["reason"].value = reason;
    }

    form.submit();
}


DZ.register({
    "submit-status-change": (element) => submitStatusChange(element)
});
