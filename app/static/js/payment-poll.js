/* ==========================================================================
   payment-poll.js — waiting for a mobile money approval

   The customer is holding their phone, waiting for a prompt from MTN,
   Telecel or AirtelTigo. That prompt can take seconds or it can take a
   minute, and nothing in the browser can hurry it.

   Two design decisions follow from that:

   1. Backoff. Polling every second for ninety seconds is 90 requests per
      customer for no benefit. The interval widens as the wait lengthens.

   2. A real ceiling with a real exit. After roughly four minutes the page
      stops polling and tells the customer what to do next, rather than
      spinning forever. The webhook still completes the payment server-side
      even after this page is closed, so stopping the poll never loses money.
   ========================================================================== */

(function () {
  "use strict";

  var root = document.querySelector("[data-poll-url]");
  if (!root) return;

  var url = root.getAttribute("data-poll-url");
  var statusText = document.querySelector("[data-poll-status]");
  var mark = document.querySelector("[data-poll-mark]");
  var fallback = document.querySelector("[data-poll-fallback]");

  var attempt = 0;
  var maxAttempts = 40;

  function delay() {
    if (attempt < 6) return 3000;
    if (attempt < 15) return 5000;
    return 9000;
  }

  function stop(message) {
    if (statusText) statusText.textContent = message;
    if (mark) mark.classList.remove("is-waiting");
    if (fallback) fallback.hidden = false;
  }

  function succeed(redirect) {
    if (mark) {
      mark.classList.remove("is-waiting");
      mark.classList.add("is-confirmed");
      mark.textContent = "✓";
    }
    if (statusText) statusText.textContent = "Payment confirmed. Taking you to your card…";
    setTimeout(function () {
      window.location.href = redirect || "/dashboard/";
    }, 1200);
  }

  function poll() {
    attempt += 1;

    if (attempt > maxAttempts) {
      stop(
        "Still waiting on your network. You can close this page — " +
          "your card activates automatically once the payment clears."
      );
      return;
    }

    fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("status " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (data.status === "success") {
          succeed(data.redirect);
          return;
        }
        if (data.status === "failed" || data.status === "abandoned") {
          stop("That payment did not go through. Nothing was charged — try again.");
          return;
        }
        setTimeout(poll, delay());
      })
      .catch(function () {
        // A dropped request is not a failed payment. Keep trying.
        setTimeout(poll, delay());
      });
  }

  setTimeout(poll, 3500);
})();
