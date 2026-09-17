/* ==========================================================================
   app.js — progressive enhancement only

   Nothing here is load-bearing. Every feature in this file degrades to a
   working, if plainer, page when JavaScript fails — which on a patchy mobile
   connection in Ghana is a normal Tuesday, not an edge case.
   ========================================================================== */

(function () {
  "use strict";

  // Marks that JS is running. The reveal styles key off .js so that content
  // is never left invisible when the script does not load.
  document.documentElement.classList.add("js");

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* --- scroll reveal ---------------------------------------------------- */
  function initReveal() {
    var targets = document.querySelectorAll(".reveal");
    if (!targets.length) return;

    if (reduceMotion || !("IntersectionObserver" in window)) {
      targets.forEach(function (el) {
        el.classList.add("is-visible");
      });
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );

    targets.forEach(function (el) {
      observer.observe(el);
    });
  }

  /* --- card tilt -------------------------------------------------------- */
  function initTilt() {
    if (reduceMotion) return;
    var cards = document.querySelectorAll("[data-tilt]");

    cards.forEach(function (card) {
      var frame = null;

      function move(event) {
        if (frame) return;
        frame = requestAnimationFrame(function () {
          var box = card.getBoundingClientRect();
          var x = (event.clientX - box.left) / box.width - 0.5;
          var y = (event.clientY - box.top) / box.height - 0.5;
          // Custom properties only. The transform itself lives in card.css.
          card.style.setProperty("--tilt-y", (x * 9).toFixed(2) + "deg");
          card.style.setProperty("--tilt-x", (-y * 9).toFixed(2) + "deg");
          frame = null;
        });
      }

      function reset() {
        card.style.setProperty("--tilt-x", "0deg");
        card.style.setProperty("--tilt-y", "0deg");
      }

      card.addEventListener("pointermove", move);
      card.addEventListener("pointerleave", reset);
      card.addEventListener("blur", reset, true);
    });
  }

  /* --- copy to clipboard ------------------------------------------------ */
  function initCopy() {
    document.querySelectorAll("[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        var value = button.getAttribute("data-copy");
        var original = button.textContent;

        function done() {
          button.textContent = "Copied";
          button.classList.add("is-confirmed");
          setTimeout(function () {
            button.textContent = original;
            button.classList.remove("is-confirmed");
          }, 1800);
        }

        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(value).then(done);
        } else {
          // http://127.0.0.1 during development has no clipboard API.
          var helper = document.createElement("textarea");
          helper.value = value;
          document.body.appendChild(helper);
          helper.select();
          try {
            document.execCommand("copy");
            done();
          } finally {
            document.body.removeChild(helper);
          }
        }
      });
    });
  }

  /* --- native share ----------------------------------------------------- */
  function initShare() {
    document.querySelectorAll("[data-share-url]").forEach(function (button) {
      if (!navigator.share) {
        button.hidden = true;
        return;
      }
      button.addEventListener("click", function () {
        navigator
          .share({
            title: button.getAttribute("data-share-title") || document.title,
            url: button.getAttribute("data-share-url")
          })
          .catch(function () {
            /* the user dismissed the sheet; nothing to do */
          });
      });
    });
  }

  /* --- live preview ----------------------------------------------------- */
  function initLivePreview() {
    var form = document.querySelector("[data-preview-form]");
    if (!form) return;

    function bind(fieldName, targetSelector, fallback) {
      var field = form.querySelector('[name="' + fieldName + '"]');
      var target = document.querySelector(targetSelector);
      if (!field || !target) return;
      field.addEventListener("input", function () {
        var value = field.value.trim();
        target.textContent = value || fallback;
        target.hidden = !value && !fallback;
      });
    }

    bind("full_name", "[data-preview-name]", "Your name");
    bind("job_title", "[data-preview-role]", "");
    bind("organisation", "[data-preview-org]", "");
    bind("bio", "[data-preview-bio]", "");
    bind("slug", "[data-preview-slug]", "your-link");

    // Colourway swatches repoint the accent custom properties on the preview.
    var accent = form.querySelector('[name="accent"]');
    var card = document.querySelector("[data-preview-card]");
    if (accent && card) {
      accent.addEventListener("change", function () {
        card.className = card.className.replace(/accent-\w+/, "accent-" + accent.value);
      });
    }
  }

  /* --- character counters ----------------------------------------------- */
  function initCounters() {
    document.querySelectorAll("[data-counter-for]").forEach(function (output) {
      var field = document.getElementById(output.getAttribute("data-counter-for"));
      if (!field) return;
      var max = parseInt(output.getAttribute("data-max"), 10) || 280;

      function update() {
        var used = field.value.length;
        output.textContent = used + " / " + max;
        output.classList.toggle("char-count-over", used > max);
      }

      field.addEventListener("input", update);
      update();
    });
  }

  /* --- checkout channel switch ------------------------------------------ */
  function initChannelSwitch() {
    var radios = document.querySelectorAll('[name="channel"]');
    var momoPanel = document.querySelector("[data-momo-panel]");
    if (!radios.length || !momoPanel) return;

    function sync() {
      var selected = document.querySelector('[name="channel"]:checked');
      momoPanel.hidden = !selected || selected.value !== "mobile_money";
    }

    radios.forEach(function (radio) {
      radio.addEventListener("change", sync);
    });
    sync();
  }

  /* --- submit locking ---------------------------------------------------
     A double-tapped Pay button is a duplicate charge. Disabling on submit is
     the cheapest possible defence, and the reference check on the server is
     the real one.
     ---------------------------------------------------------------------- */
  function initSubmitGuard() {
    document.querySelectorAll("form[data-guard]").forEach(function (form) {
      form.addEventListener("submit", function () {
        var button = form.querySelector('button[type="submit"], input[type="submit"]');
        if (!button) return;
        setTimeout(function () {
          button.disabled = true;
          button.textContent = button.getAttribute("data-busy-label") || "Working…";
        }, 0);
      });
    });
  }

  /* --- flash dismissal --------------------------------------------------- */
  function initFlash() {
    document.querySelectorAll(".flash").forEach(function (flash) {
      var close = flash.querySelector(".flash-close");
      function dismiss() {
        flash.hidden = true;
      }
      if (close) close.addEventListener("click", dismiss);
      if (!flash.classList.contains("flash-error")) {
        setTimeout(dismiss, 6000);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initReveal();
    initTilt();
    initCopy();
    initShare();
    initLivePreview();
    initCounters();
    initChannelSwitch();
    initSubmitGuard();
    initFlash();
  });
})();
