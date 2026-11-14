/**
 * A small DOM stub for the client modules.
 *
 * The project declares jsdom as a devDependency, but these tests are written
 * to run with no `npm install` at all, matching tests/send.test.mjs and
 * tests/photon-service.test.mjs. The modules under test touch a narrow,
 * stable slice of the DOM, so faking it is cheap and keeps `node --test`
 * usable on a clean checkout.
 *
 * Install with `installDOM()` and reset between tests with `resetDOM()`.
 */

export class FakeElement {
  constructor(tagName = 'div') {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.classList = makeClassList(this);
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.files = null;
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.id = '';
    this.title = '';
    this.type = '';
    this.src = '';
    this.alt = '';
    this.htmlFor = '';
    this.placeholder = '';
    this.maxLength = -1;
    this.autocomplete = '';
    this.accept = '';
    this.noValidate = false;
    this.tabIndex = 0;
    this.onclick = null;
    this.onsubmit = null;
    this.focused = false;
  }

  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === globalThis.document?.body;
  }

  set innerHTML(value) {
    if (value === '') this.replaceChildren();
  }
  get innerHTML() {
    return this.children.map(child => child.textContent).join('');
  }

  append(...nodes) {
    for (const node of nodes) {
      if (typeof node === 'string') {
        this.children.push(Object.assign(new FakeElement('#text'), { textContent: node }));
        continue;
      }
      node.parentNode = this;
      this.children.push(node);
    }
  }
  appendChild(node) {
    this.append(node);
    return node;
  }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }
  remove() {
    const parent = this.parentNode;
    if (!parent) return;
    parent.children = parent.children.filter(child => child !== this);
    this.parentNode = null;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter(fn => fn !== handler);
  }

  focus() {
    this.focused = true;
    if (globalThis.document) globalThis.document.activeElement = this;
  }

  contains(node) {
    if (node === this) return true;
    return this.descendants().includes(node);
  }

  descendants() {
    const out = [];
    for (const child of this.children) {
      out.push(child, ...child.descendants());
    }
    return out;
  }

  /** Match one compound token: tag, `.class`, or `#id`. */
  matchesToken(token) {
    if (token.startsWith('.')) return this.classList.contains(token.slice(1));
    if (token.startsWith('#')) return this.id === token.slice(1);
    return this.tagName === token.toUpperCase();
  }

  /** Match on tag, `.class`, or `#id`; comma-separated selectors allowed. */
  matches(selector) {
    return selector
      .split(',')
      .map(part => part.trim())
      .some(part => part.split(/\s+/).every(token => this.matchesToken(token)));
  }

  querySelectorAll(selector) {
    return selector.split(',').flatMap(part => this.queryDescendant(part.trim()));
  }

  /** Supports descendant combinators ("a b c"), which the modules do use. */
  queryDescendant(selector) {
    const tokens = selector.split(/\s+/).filter(Boolean);
    let scope = [this];
    let matched = [];
    for (const token of tokens) {
      matched = scope.flatMap(node =>
        node.descendants().filter(child => child.matchesToken(token)),
      );
      scope = matched;
    }
    return [...new Set(matched)];
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  /** Fire a click, awaiting any async handler. */
  async click() {
    if (this.disabled) return;
    if (this.onclick) await this.onclick();
    for (const handler of this.listeners.click || []) await handler();
  }

  async submit() {
    const event = { preventDefault() {} };
    if (this.onsubmit) await this.onsubmit(event);
    for (const handler of this.listeners.submit || []) await handler(event);
  }

  async keydown(key) {
    const event = { key, preventDefault() {} };
    for (const handler of this.listeners.keydown || []) await handler(event);
  }

  get childElementCount() {
    return this.children.length;
  }
}

function makeClassList(element) {
  return {
    add(...names) {
      const set = new Set(element.className.split(/\s+/).filter(Boolean));
      for (const name of names) set.add(name);
      element.className = [...set].join(' ');
    },
    remove(...names) {
      const set = new Set(element.className.split(/\s+/).filter(Boolean));
      for (const name of names) set.delete(name);
      element.className = [...set].join(' ');
    },
    toggle(name, force) {
      const has = this.contains(name);
      const next = force === undefined ? !has : force;
      next ? this.add(name) : this.remove(name);
      return next;
    },
    contains(name) {
      return element.className.split(/\s+/).includes(name);
    },
  };
}

class FakeAudio {
  static created = [];
  constructor(src = '') {
    this.src = src;
    this.currentTime = 0;
    this.preload = '';
    this.played = false;
    FakeAudio.created.push(this);
  }
  async play() {
    this.played = true;
    if (FakeAudio.playRejects) throw new Error('autoplay blocked');
  }
  pause() {}
}

export function installDOM({ ids = [] } = {}) {
  const body = new FakeElement('body');
  const registry = new Map();

  const document = {
    body,
    activeElement: null,
    createElement: tag => new FakeElement(tag),
    createTextNode: text => Object.assign(new FakeElement('#text'), { textContent: text }),
    getElementById: id => registry.get(id) ?? null,
    querySelector: selector => body.querySelector(selector),
    querySelectorAll: selector => body.querySelectorAll(selector),
    addEventListener() {},
    dispatchEvent() {},
  };

  for (const id of ids) {
    const element = new FakeElement('div');
    element.id = id;
    registry.set(id, element);
    body.append(element);
  }

  globalThis.document = document;
  globalThis.Audio = FakeAudio;
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  };
  // Assigned unconditionally: Node's own createObjectURL demands a real Blob
  // and would throw on the lightweight stand-ins these tests pass around.
  globalThis.URL.createObjectURL = () => 'blob:stub';
  globalThis.URL.revokeObjectURL = () => {};
  globalThis.window = globalThis;

  return { document, body, registry, Audio: FakeAudio };
}

export function resetDOM() {
  FakeAudio.created = [];
  FakeAudio.playRejects = false;
  globalThis.speechSynthesis = undefined;
  globalThis.SpeechSynthesisUtterance = undefined;
}

export { FakeAudio };
