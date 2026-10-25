function copyReminder() {

    const text =
        document.getElementById("reminderSubject").value + "\n\n" +
        document.getElementById("reminderBody").value;

    const hint = document.getElementById("copyHint");

    const done = () => { hint.hidden = false; setTimeout(() => { hint.hidden = true; }, 2000); };

    if (navigator.clipboard && navigator.clipboard.writeText) {

        navigator.clipboard.writeText(text).then(done);

    } else {

        const area = document.getElementById("reminderBody");
        area.select();
        document.execCommand("copy");
        done();

    }

}

// mailto odkaz sa pri úprave textu zostaví nanovo
(function () {

    const link = document.getElementById("mailtoLink");

    if (!link) { return; }

    const email = link.getAttribute("href").split("?")[0];

    function rebuild() {

        link.setAttribute(
            "href",
            email + "?subject=" + encodeURIComponent(document.getElementById("reminderSubject").value) +
            "&body=" + encodeURIComponent(document.getElementById("reminderBody").value)
        );

    }

    document.getElementById("reminderBody").addEventListener("input", rebuild);

})();


DZ.register({
    "copy-reminder": () => copyReminder()
});
