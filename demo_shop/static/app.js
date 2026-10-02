// Kulhad & Co.: a demo shop for Nightshift. Plain JS, no build step.
// Planted bugs are switched on by the server through window.BUGS, and legitimate
// UI changes through window.VARIANTS (demo_shop/server.py). Every bug is marked BUG.
"use strict";

const BUGS = new Set(window.BUGS || []);
const VARIANTS = new Set(window.VARIANTS || []);
const view = document.getElementById("view");
let products = [];
const state = loadState();
const countAtLoad = state.cart.reduce((n, line) => n + line.qty, 0);

function loadState() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("kulhad"));
    if (saved && Array.isArray(saved.cart)) return saved;
  } catch (_) {
    // unreadable storage: start fresh
  }
  return { user: null, cart: [], lastOrder: null };
}

function saveState() {
  sessionStorage.setItem("kulhad", JSON.stringify(state));
}

const rupees = (n) => "₹" + n.toLocaleString("en-IN");
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const productById = (id) => products.find((p) => p.id === id);
const cartCount = () => state.cart.reduce((n, line) => n + line.qty, 0);
// BUG price-mismatch: the cart prices the kulhad at ₹480; the shop lists it at ₹420.
const unitPrice = (id) => (BUGS.has("price-mismatch") && id === "kulhad" ? 480 : productById(id).price);
const cartTotal = () => state.cart.reduce((sum, line) => sum + line.qty * unitPrice(line.id), 0);

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

// The toast stays until the next one replaces it, so "did that click do anything?"
// never depends on how fast someone looked.
function toast(message) {
  document.getElementById("toast").textContent = message;
}

function renderHeader() {
  const cartLink = document.getElementById("cart-link");
  // BUG cart-count-stale: the count is the one from page load, never updated.
  cartLink.textContent = `Cart (${BUGS.has("cart-count-stale") ? countAtLoad : cartCount()})`;
  // BUG cart-link-404: a typo in the route.
  cartLink.setAttribute("href", BUGS.has("cart-link-404") ? "#/kart" : "#/cart");
  const account = document.getElementById("account");
  if (state.user) {
    account.innerHTML = `Hi, ${esc(state.user.name)} <button type="button" class="link" id="logout">Log out</button>`;
    document.getElementById("logout").onclick = () => {
      if (!BUGS.has("logout-broken")) state.user = null; // BUG logout-broken: says goodbye, keeps the session
      saveState();
      toast("You have logged out.");
      route();
    };
  } else {
    account.innerHTML = `<a href="#/login">Log in</a>`;
  }
}

function addToCart(id) {
  if (BUGS.has("dead-add-button") && id === "kulhad") return; // BUG: the click is swallowed
  if (BUGS.has("add-wrong-product") && id === "coffee") id = "chai"; // BUG: the button is wired to the wrong product
  const qty = BUGS.has("qty-double") ? 2 : 1; // BUG qty-double
  const line = state.cart.find((l) => l.id === id);
  if (line) line.qty += qty;
  else state.cart.push({ id, qty });
  saveState();
  renderHeader();
  toast(`Added ${productById(id).name} to your cart.`);
}

function viewProducts() {
  document.title = "Kulhad & Co. | tea, coffee and things to drink them from";
  view.innerHTML = `
    <h1>Shop</h1>
    <input type="search" id="search" class="search" placeholder="Search products" aria-label="Search products">
    <p id="no-results" class="muted"></p>
    <ul class="grid">
      ${products.map((p) => `
        <li class="card" data-name="${esc(p.name.toLowerCase())}">
          <h2>${esc(p.name)}</h2>
          <p class="blurb">${esc(p.blurb)}</p>
          <p class="price">${rupees(p.price)}</p>
          <button type="button" data-add="${p.id}" aria-label="Add ${esc(p.name)} to cart">Add to cart</button>
        </li>`).join("")}
    </ul>`;
  view.querySelectorAll("[data-add]").forEach((button) => {
    button.onclick = () => addToCart(button.dataset.add);
  });
  const search = document.getElementById("search");
  search.oninput = () => {
    const query = search.value.trim().toLowerCase();
    let shown = 0;
    view.querySelectorAll(".card").forEach((card) => {
      // BUG search-broken: matches against an attribute that doesn't exist.
      const name = BUGS.has("search-broken") ? card.dataset.title || "" : card.dataset.name;
      const match = !query || name.includes(query);
      card.hidden = !match;
      if (match) shown += 1;
    });
    document.getElementById("no-results").textContent = shown ? "" : `No products match "${search.value.trim()}".`;
  };
}

function viewLogin(params) {
  document.title = "Log in | Kulhad & Co.";
  const next = params.get("next") || "";
  view.innerHTML = `
    <h1>Log in</h1>
    ${next === "checkout" ? "<p>Log in to check out. Your cart is saved.</p>" : ""}
    <form id="login-form" class="form" novalidate>
      <label>Email <input name="email" type="email" autocomplete="username"></label>
      <label>Password <input name="password" type="password" autocomplete="current-password"></label>
      <p class="error" id="login-error" role="alert"></p>
      <button type="submit">Log in</button>
    </form>
    <p><a href="#/login-code${next ? `?next=${encodeURIComponent(next)}` : ""}">Sign in with an email code instead</a></p>
    <p><a href="#/login-phone${next ? `?next=${encodeURIComponent(next)}` : ""}">Log in with your mobile number</a></p>`;
  const form = document.getElementById("login-form");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const { ok, data } = await api("/api/login", Object.fromEntries(new FormData(form)));
    if (!ok) {
      document.getElementById("login-error").textContent = data.error || "Could not log in.";
      return;
    }
    state.user = data.user;
    // BUG guest-cart-lost: logging in mid-checkout swaps the guest cart for the account's (empty) one.
    if (BUGS.has("guest-cart-lost") && next === "checkout") state.cart = [];
    saveState();
    toast(`Welcome back, ${data.user.name}.`);
    location.hash = next ? `#/${next}` : "#/";
  };
}

// Sign in with a one-time code sent by email. The "email" lands in the shop's outbox,
// readable at /mail like a Mailpit inbox, which is how Nightshift's test inbox finds it.
function viewLoginCode(params) {
  document.title = "Sign in with a code | Kulhad & Co.";
  const next = params.get("next") || "";
  view.innerHTML = `
    <h1>Sign in with an email code</h1>
    <form id="code-request" class="form" novalidate>
      <label>Email <input name="email" type="email" autocomplete="username"></label>
      <button type="submit">Email me a code</button>
    </form>
    <form id="code-verify" class="form" novalidate hidden>
      <p id="code-sent"></p>
      <label>Sign-in code <input name="code" inputmode="numeric" autocomplete="one-time-code"></label>
      <p class="error" id="code-error" role="alert"></p>
      <button type="submit">Sign in</button>
    </form>`;
  const request = document.getElementById("code-request");
  const verify = document.getElementById("code-verify");
  let email = "";
  request.onsubmit = async (event) => {
    event.preventDefault();
    email = new FormData(request).get("email").trim();
    await api("/api/login-code", { email });
    request.hidden = true;
    verify.hidden = false;
    document.getElementById("code-sent").textContent = `We emailed a 6-digit code to ${email}.`;
  };
  verify.onsubmit = async (event) => {
    event.preventDefault();
    const { ok, data } = await api("/api/login-code/verify", { email, code: new FormData(verify).get("code") });
    if (!ok) {
      document.getElementById("code-error").textContent = data.error || "Could not sign in.";
      return;
    }
    state.user = data.user;
    saveState();
    toast(`Welcome back, ${data.user.name}.`);
    location.hash = next ? `#/${next}` : "#/";
  };
}

// Log in with a mobile number and an OTP by SMS, the way most Indian apps do: a +91 box that
// formats the number as you type ("98765 43210"), then six one-digit boxes that move along by
// themselves. The "SMS" lands in the shop's /sms inbox, where Nightshift reads {{sms_code}}.
function viewLoginPhone(params) {
  document.title = "Log in with your mobile | Kulhad & Co.";
  const next = params.get("next") || "";
  const boxes = [1, 2, 3, 4, 5, 6].map((i) =>
    `<input class="otp-box" inputmode="numeric" maxlength="1" autocomplete="${i === 1 ? "one-time-code" : "off"}" aria-label="OTP digit ${i}">`).join("");
  view.innerHTML = `
    <h1>Log in with your mobile number</h1>
    <form id="phone-request" class="form" novalidate>
      <label>Mobile number <span class="prefix">+91</span><input name="phone" type="tel" inputmode="numeric" autocomplete="tel-national" maxlength="11"></label>
      <p class="error" id="phone-error" role="alert"></p>
      <button type="submit">Send OTP</button>
    </form>
    <form id="otp-verify" class="form" novalidate hidden>
      <p id="otp-sent"></p>
      <fieldset class="otp"><legend>Enter the 6-digit OTP</legend>${boxes}</fieldset>
      <p class="error" id="otp-error" role="alert"></p>
      <button type="submit">Verify and log in</button>
    </form>`;
  const request = document.getElementById("phone-request");
  const verify = document.getElementById("otp-verify");
  const phoneBox = request.elements.phone;
  phoneBox.oninput = () => {
    const digits = phoneBox.value.replace(/\D/g, "").slice(0, 10);
    phoneBox.value = digits.length > 5 ? `${digits.slice(0, 5)} ${digits.slice(5)}` : digits;
  };
  const otp = [...verify.querySelectorAll(".otp-box")];
  otp.forEach((box, i) => {
    box.oninput = () => {
      box.value = box.value.replace(/\D/g, "").slice(-1);
      if (box.value && otp[i + 1]) otp[i + 1].focus();
    };
    box.onkeydown = (event) => {
      if (event.key === "Backspace" && !box.value && otp[i - 1]) otp[i - 1].focus();
    };
  });
  let phone = "";
  request.onsubmit = async (event) => {
    event.preventDefault();
    phone = phoneBox.value;
    const { ok, data } = await api("/api/phone-code", { phone });
    if (!ok) {
      document.getElementById("phone-error").textContent = data.error || "Could not send the OTP.";
      return;
    }
    request.hidden = true;
    verify.hidden = false;
    document.getElementById("otp-sent").textContent = `We sent an OTP by SMS to +91 ${phone}.`;
    otp[0].focus();
  };
  verify.onsubmit = async (event) => {
    event.preventDefault();
    const { ok, data } = await api("/api/phone-code/verify", { phone, code: otp.map((box) => box.value).join("") });
    if (!ok) {
      document.getElementById("otp-error").textContent = data.error || "Could not log in.";
      return;
    }
    state.user = data.user;
    saveState();
    toast(`Welcome back, ${data.user.name}.`);
    location.hash = next ? `#/${next}` : "#/";
  };
}

function viewCart() {
  document.title = "Cart | Kulhad & Co.";
  if (!state.cart.length) {
    view.innerHTML = `<h1>Your cart</h1><p>Your cart is empty. <a href="#/">Browse the shop</a></p>`;
    return;
  }
  const lines = state.cart.map((line) => ({ ...line, product: productById(line.id), price: unitPrice(line.id) }));
  // BUG wrong-total: the total forgets the last line.
  const counted = BUGS.has("wrong-total") ? lines.slice(0, -1) : lines;
  const total = counted.reduce((sum, l) => sum + l.qty * l.price, 0);
  const checkoutButton = VARIANTS.has("redesign")
    ? `<button type="button" id="go-checkout" class="cta">Go to checkout</button>`
    : `<button type="button" id="checkout">Proceed to checkout</button>`;
  view.innerHTML = `
    <h1>Your cart</h1>
    <div class="wrap">
    <table class="cart">
      <thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Line total</th><th></th></tr></thead>
      <tbody>
        ${lines.map((l) => `
          <tr>
            <td>${esc(l.product.name)}</td>
            <td>${l.qty}</td>
            <td>${rupees(l.price)}</td>
            <td>${rupees(l.qty * l.price)}</td>
            <td><button type="button" class="link" data-remove="${l.id}" aria-label="Remove ${esc(l.product.name)}">Remove</button></td>
          </tr>`).join("")}
      </tbody>
    </table>
    </div>
    <p class="total">Total: <strong>${rupees(total)}</strong></p>
    ${checkoutButton}`;
  view.querySelectorAll("[data-remove]").forEach((button) => {
    button.onclick = () => {
      // BUG remove-wrong-item: always removes the last line.
      const id = BUGS.has("remove-wrong-item") ? state.cart[state.cart.length - 1].id : button.dataset.remove;
      state.cart = state.cart.filter((l) => l.id !== id);
      saveState();
      route();
    };
  });
  view.querySelector("#checkout, #go-checkout").onclick = () => {
    location.hash = state.user ? "#/checkout" : "#/login?next=checkout";
  };
  // BUG js-error: an analytics call on an object that never loaded. The page looks
  // fine; only the browser's error log knows.
  if (BUGS.has("js-error")) setTimeout(() => window.analytics.track("view_cart", { items: lines.length }), 0);
}

function viewCheckout() {
  document.title = "Checkout | Kulhad & Co.";
  if (!state.user) {
    location.hash = "#/login?next=checkout";
    return;
  }
  if (!state.cart.length) {
    location.hash = "#/cart";
    return;
  }
  // BUG place-order-covered: a transparent promo layer sits on the button and eats every click.
  const cover = BUGS.has("place-order-covered") ? `<div class="promo-layer"></div>` : "";
  view.innerHTML = `
    <h1>Checkout</h1>
    <form id="checkout-form" class="form" novalidate>
      <label>Full name <input name="name" autocomplete="name"></label>
      <label>Address <input name="address" autocomplete="street-address"></label>
      <label>City <input name="city" autocomplete="address-level2"></label>
      <label>Pincode <input name="pincode" inputmode="numeric" autocomplete="postal-code"></label>
      <fieldset>
        <legend>Payment</legend>
        <label class="inline"><input type="radio" name="payment" value="cod"> Cash on delivery</label>
        <label class="inline"><input type="radio" name="payment" value="upi"> UPI on delivery</label>
        <label class="inline"><input type="radio" name="payment" value="online"> Pay online now (UPI)</label>
      </fieldset>
      <p class="total">Order total: <strong>${rupees(cartTotal())}</strong></p>
      <p class="error" id="checkout-error" role="alert"></p>
      <div class="submit-wrap"><button type="submit">Place order</button>${cover}</div>
    </form>`;
  const form = document.getElementById("checkout-form");
  const error = document.getElementById("checkout-error");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(form));
    if (!fields.name.trim() || !fields.address.trim() || !fields.city.trim()) {
      error.textContent = "Please fill in your name, address and city.";
      return;
    }
    // BUG pincode-accepts-5: the check allows 5 digits.
    const pincode = BUGS.has("pincode-accepts-5") ? /^\d{5,6}$/ : /^\d{6}$/;
    if (!pincode.test(fields.pincode.trim())) {
      // BUG validation-message-missing: blocks the order but says nothing.
      error.textContent = BUGS.has("validation-message-missing") ? "" : "Pincode must be 6 digits.";
      return;
    }
    if (!fields.payment) {
      error.textContent = "Choose a payment method.";
      return;
    }
    error.textContent = "";
    if (fields.payment === "online") {
      payOnline(fields, error);
      return;
    }
    await placeOrder(fields, error);
  };
}

async function placeOrder(fields, error) {
  const { ok, data } = await api("/api/order", { ...fields, items: state.cart });
  if (!ok) {
    error.textContent = "Something went wrong. Please try again.";
    return;
  }
  state.lastOrder = data;
  state.cart = [];
  saveState();
  location.hash = "#/order";
}

// "Pay online" opens the gateway's checkout in an iframe on another origin (localhost vs
// 127.0.0.1), the way Razorpay and other gateways embed theirs, and waits for its message.
function payOnline(fields, error) {
  const other = location.hostname === "localhost" ? "127.0.0.1" : "localhost";
  const ref = "KP" + Date.now();
  const src = `${location.protocol}//${other}:${location.port}/gateway.html?amount=${cartTotal()}&ref=${ref}`;
  const box = document.createElement("div");
  box.className = "gateway";
  box.innerHTML = `<p>Complete the payment below.</p>
    <iframe title="Kulhad Pay" src="${src}" width="440" height="340" style="border:1px solid #ccc"></iframe>`;
  document.getElementById("checkout-form").after(box);
  const onMessage = async (event) => {
    if (!event.data || event.data.type !== "kulhad-pay" || event.data.ref !== ref) return;
    window.removeEventListener("message", onMessage);
    box.remove();
    // BUG payment-failure-ignored: the order goes through whether or not the payment did.
    if (event.data.status !== "paid" && !BUGS.has("payment-failure-ignored")) {
      error.textContent = "Payment failed. Your order was not placed.";
      return;
    }
    await placeOrder(fields, error);
  };
  window.addEventListener("message", onMessage);
}

function viewOrder() {
  document.title = "Order placed | Kulhad & Co.";
  const order = state.lastOrder;
  if (!order) {
    location.hash = "#/";
    return;
  }
  view.innerHTML = `
    <h1>Order placed!</h1>
    <p>Order number: <strong>${esc(order.orderId)}</strong></p>
    <p>${order.payment === "online" ? `Paid ${rupees(order.total)} online.`
      : `Pay ${rupees(order.total)} ${order.payment === "upi" ? "by UPI" : "in cash"} when it arrives.`}</p>
    <p><a href="#/">Continue shopping</a></p>`;
}

function viewNotFound() {
  document.title = "Not found | Kulhad & Co.";
  view.innerHTML = `<h1>Page not found</h1><p><a href="#/">Back to the shop</a></p>`;
}

const views = { "": viewProducts, login: viewLogin, "login-code": viewLoginCode, "login-phone": viewLoginPhone, cart: viewCart, checkout: viewCheckout, order: viewOrder };

function route() {
  const [name, query] = location.hash.replace(/^#\/?/, "").split("?");
  renderHeader();
  (views[name] || viewNotFound)(new URLSearchParams(query || ""));
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);

(async function start() {
  const { data } = await api("/api/products");
  products = data;
  route();
})();
