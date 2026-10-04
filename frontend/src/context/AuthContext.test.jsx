import { act } from "react";
import { createRoot } from "react-dom/client";
import { api, SESSION_ENDED_EVENT } from "@/lib/api";
import { AuthProvider, useAuth } from "@/context/AuthContext";

// 2026-09-26: a tab whose session had ended kept showing the signed-in app, every
// call came back 401, and the Jobs page rendered no jobs - so saved jobs looked
// deleted while they were still in the database and running.

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const USER = { user_id: "user_1", email: "someone@example.com", name: "Someone" };

function reply(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

let routes;
let calls;

beforeEach(() => {
  calls = [];
  routes = {};
  global.fetch = jest.fn((url) => {
    const path = new URL(url).pathname.replace(/^\/api/, "");
    calls.push(path);
    const handler = routes[path];
    return handler ? handler() : reply(404, { detail: "Not Found" });
  });
});

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("api", () => {
  test("a 401 from an app call says the session ended", async () => {
    routes["/jobs"] = () => reply(401, { detail: "Invalid session" });
    const heard = jest.fn();
    window.addEventListener(SESSION_ENDED_EVENT, heard);
    await expect(api("/jobs")).rejects.toMatchObject({ status: 401 });
    window.removeEventListener(SESSION_ENDED_EVENT, heard);
    expect(heard).toHaveBeenCalledTimes(1);
  });

  test("sign-in calls and other errors do not", async () => {
    routes["/auth/me"] = () => reply(401, { detail: "Not authenticated" });
    routes["/auth/login"] = () => reply(401, { detail: "Invalid email or password" });
    routes["/jobs"] = () => reply(500, { detail: "boom" });
    const heard = jest.fn();
    window.addEventListener(SESSION_ENDED_EVENT, heard);
    await expect(api("/auth/me")).rejects.toMatchObject({ status: 401 });
    await expect(api("/auth/login", { method: "POST", body: {} })).rejects.toMatchObject({ status: 401 });
    await expect(api("/jobs")).rejects.toMatchObject({ status: 500 });
    window.removeEventListener(SESSION_ENDED_EVENT, heard);
    expect(heard).not.toHaveBeenCalled();
  });
});

describe("AuthProvider", () => {
  let root;
  let seen;

  function Probe() {
    seen = useAuth();
    return null;
  }

  async function signedIn() {
    routes["/auth/me"] = () => reply(200, USER);
    root = createRoot(document.createElement("div"));
    // The component is rendered through react-dom's createRoot, not Testing Library.
    // eslint-disable-next-line testing-library/no-unnecessary-act
    await act(async () => {
      root.render(
        <AuthProvider>
          <Probe />
        </AuthProvider>,
      );
      await settle();
    });
    expect(seen.user).toEqual(USER);
    expect(seen.sessionEnded).toBe(false);
  }

  afterEach(() => {
    act(() => root.unmount());
  });

  test("a lost session signs the tab out and says why", async () => {
    await signedIn();
    routes["/auth/me"] = () => reply(401, { detail: "Invalid session" });
    routes["/jobs"] = () => reply(401, { detail: "Invalid session" });
    await act(async () => {
      await api("/jobs").catch(() => {});
      await settle();
    });
    expect(seen.user).toBeNull();
    expect(seen.sessionEnded).toBe(true);
  });

  test("a 401 while the session is still good leaves the tab signed in", async () => {
    await signedIn();
    routes["/jobs"] = () => reply(401, { detail: "provider refused" });
    await act(async () => {
      await api("/jobs").catch(() => {});
      await settle();
    });
    expect(seen.user).toEqual(USER);
    expect(seen.sessionEnded).toBe(false);
  });

  test("many failing calls ask the server once", async () => {
    await signedIn();
    let answer;
    routes["/auth/me"] = () => new Promise((resolve) => { answer = resolve; });
    routes["/jobs"] = () => reply(401, { detail: "Invalid session" });
    routes["/requests/version"] = () => reply(401, { detail: "Invalid session" });
    calls = [];
    await act(async () => {
      await Promise.all([
        api("/jobs").catch(() => {}),
        api("/requests/version").catch(() => {}),
        api("/jobs").catch(() => {}),
      ]);
      await settle();
    });
    expect(calls.filter((path) => path === "/auth/me")).toHaveLength(1);
    await act(async () => {
      answer(reply(401, { detail: "Invalid session" }));
      await settle();
      await settle();
    });
    expect(seen.user).toBeNull();
    expect(seen.sessionEnded).toBe(true);
  });

  test("signing in again clears the notice", async () => {
    await signedIn();
    routes["/auth/me"] = () => reply(401, { detail: "Invalid session" });
    routes["/jobs"] = () => reply(401, { detail: "Invalid session" });
    await act(async () => {
      await api("/jobs").catch(() => {});
      await settle();
    });
    expect(seen.sessionEnded).toBe(true);
    routes["/auth/login"] = () => reply(200, USER);
    await act(async () => {
      await seen.login("someone@example.com", "x");
    });
    expect(seen.user).toEqual(USER);
    expect(seen.sessionEnded).toBe(false);
  });
});
