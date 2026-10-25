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
