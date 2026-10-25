/*
 * Spoločný skript appky (načítava sa na každej stránke).
 *
 *  - téma (svetlá/tmavá),
 *  - delegované obsluhy udalostí cez data- atribúty, aby šablóny nepotrebovali
 *    inline handlery (onclick=...), ktoré Content-Security-Policy zakazuje:
 *
 *      data-action="nazov"            klik -> DZ.actions[nazov](prvok, udalost)
 *      data-change-action="nazov"     zmena (select...) -> DZ.actions[nazov](prvok, udalost)
 *      data-auto-submit               zmena odošle formulár
 *      <form data-confirm="Text?">    pred odoslaním sa opýta (Zrušiť = neodošle)
 *
 *    Stránkové skripty (static/js/pages/*.js) registrujú svoje akcie cez DZ.register({...}).
 *    Volať sa dajú len zaregistrované akcie, nie ľubovoľné globálne funkcie.
 */
(function () {

    "use strict";

    var THEME_KEY = "digitalnyZositTheme";

    function applyTheme(theme) {

        document.documentElement.setAttribute("data-theme", theme);

        var button = document.getElementById("themeButton");

        if (button) {
            button.textContent = theme === "dark" ? "☀️" : "🌙";
        }

    }

    function readTheme() {

        try {
            return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
        } catch (error) {
            return "light";
        }

    }

    function toggleTheme() {

        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";

        try {
            localStorage.setItem(THEME_KEY, next);
        } catch (error) {
            /* súkromný režim: téma platí len pre túto stránku */
        }

        applyTheme(next);

    }

    var DZ = window.DZ = {

        actions: {"toggle-theme": toggleTheme},

        register: function (actions) {
            Object.keys(actions).forEach(function (name) {
                DZ.actions[name] = actions[name];
            });
        }

    };

    function run(name, element, event) {

        if (Object.prototype.hasOwnProperty.call(DZ.actions, name)) {
            DZ.actions[name](element, event);
        }

    }

    document.addEventListener("click", function (event) {

        var element = event.target.closest ? event.target.closest("[data-action]") : null;

        if (element) {
            run(element.getAttribute("data-action"), element, event);
        }

    });

    document.addEventListener("change", function (event) {

        var target = event.target;

        if (!target || !target.closest) {
            return;
        }

        var action = target.closest("[data-change-action]");

        if (action) {
            run(action.getAttribute("data-change-action"), action, event);
            return;
        }

        if (target.hasAttribute("data-auto-submit") && target.form) {
            target.form.submit();
        }

    });

    document.addEventListener("submit", function (event) {

        var message = event.target && event.target.getAttribute ? event.target.getAttribute("data-confirm") : null;

        if (message && !window.confirm(message)) {
            event.preventDefault();
        }

    });

    applyTheme(readTheme());

})();
