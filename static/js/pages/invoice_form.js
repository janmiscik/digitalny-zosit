/* =====================================
   DÁTUM SPLATNOSTI - predvyplnenie (+14 dní od vystavenia)
   ===================================== */

(function() {

    const issueInput = document.querySelector('input[name="issue_date"]');
    const dueInput = document.querySelector('input[name="due_date"]');

    function setDefaultDueDate() {

        if (dueInput.value) {
            return;
        }

        const issueValue = issueInput.value;

        if (!issueValue) {
            return;
        }

        const issueDate = new Date(issueValue);
        issueDate.setDate(issueDate.getDate() + 14);

        const yyyy = issueDate.getFullYear();
        const mm = String(issueDate.getMonth() + 1).padStart(2, "0");
        const dd = String(issueDate.getDate()).padStart(2, "0");

        dueInput.value = `${yyyy}-${mm}-${dd}`;

    }

    setDefaultDueDate();

    issueInput.addEventListener("change", setDefaultDueDate);

})();


/* =====================================
   DYNAMICKÉ RIADKY POLOŽIEK
   ===================================== */

function addItemRow() {

    const template =
        document.getElementById("itemRowTemplate");

    const clone =
        template.content.cloneNode(true);

    document
        .getElementById("itemsBody")
        .appendChild(clone);

}


function removeItemRow(button) {

    const rows =
        document.querySelectorAll(".invoice-item-row");

    if (rows.length <= 1) {
        return;
    }

    button
        .closest("tr")
        .remove();

}


DZ.register({
    "add-item-row": () => addItemRow(),
    "remove-item-row": (element) => removeItemRow(element)
});
