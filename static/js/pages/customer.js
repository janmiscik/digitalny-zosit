function openJobForm() {

    document
        .getElementById("jobModal")
        .classList.add("active");

}


function closeJobForm() {

    document
        .getElementById("jobModal")
        .classList.remove("active");

}


document
    .getElementById("jobModal")
    .addEventListener(
        "click",
        function(event) {

            if (event.target === this) {

                closeJobForm();

            }

        }
    );


DZ.register({
    "open-job-form": () => openJobForm(),
    "close-job-form": () => closeJobForm()
});
