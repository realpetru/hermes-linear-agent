/**
 * Linear agent — dashboard plugin (Configure surface with a Monitor strip).
 *
 * Built against the SDK contract in web/src/plugins/registry.ts:
 *   SDK.fetchJSON for /api/plugins/linear-agent/*, SDK.utils.cn, SDK.sdkVersion.
 *   No toast: the action bar owns save state (see the design spec, §7).
 *
 * Plain IIFE, React.createElement, no build step, no bundled React.
 */
(function () {
  "use strict";

  var registry = window.__HERMES_PLUGINS__;
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!registry || typeof registry.register !== "function" || !SDK || !SDK.React) return;

  var React = SDK.React;
  var h = React.createElement;
  var hooks = SDK.hooks || {};
  var useState = hooks.useState || React.useState;
  var useEffect = hooks.useEffect || React.useEffect;
  var useCallback = hooks.useCallback || React.useCallback;
  var useMemo = hooks.useMemo || React.useMemo;
  var C = SDK.components || {};
  var utils = SDK.utils || {};
  var cn =
    typeof utils.cn === "function"
      ? utils.cn
      : function () {
          return Array.prototype.slice.call(arguments).filter(Boolean).join(" ");
        };

  var Card = C.Card || fb("div", "la-card");
  var CardContent = C.CardContent || fb("div", "la-card-body");
  var Button = C.Button || fb("button", "la-button");
  var Input = C.Input || fb("input", "la-input");

  var API = "/api/plugins/linear-agent";
  var SUPPORTED_SDK_MAJOR = 1;
  var PAGE_SIZE = 9;

  function fb(tag, base) {
    return function Fallback(props) {
      var rest = Object.assign({}, props);
      var children = rest.children;
      rest.className = cn(base, rest.className);
      delete rest.children;
      return h(tag, rest, children);
    };
  }

  function initials(name) {
    // three letters so cody and commy are distinguishable at 30px
    var clean = String(name || "").replace(/[^a-zA-Z0-9]/g, "");
    if (!clean) return "DEF";
    return clean.slice(0, 3).toUpperCase();
  }

  function messageOf(err) {
    if (!err) return "unknown error";
    return String(err.message || err.detail || err);
  }

  function sdkMajor(v) {
    var n = parseInt(String(v || "0").split(".")[0], 10);
    return isNaN(n) ? 0 : n;
  }

  // ---------------------------------------------------------------- pieces
  function Banner(props) {
    return h(
      "div",
      { className: cn("la-banner", props.tone === "danger" ? "la-banner-danger" : "la-banner-warn") },
      h("div", { className: "la-banner-text" }, h("strong", null, props.title), h("span", null, props.body)),
      props.detail ? h("code", { className: "la-banner-detail" }, props.detail) : null,
      props.action
        ? h(Button, { type: "button", className: cn("la-button-quiet"), onClick: props.action.onClick }, props.action.label)
        : null
    );
  }

  function ProfileCard(props) {
    return h(
      "button",
      {
        type: "button",
        className: cn("la-profile", props.selected && "la-profile-selected"),
        "aria-pressed": props.selected ? "true" : "false",
        disabled: props.disabled,
        onClick: function () {
          props.onSelect(props.value);
        },
      },
      h("span", { className: "la-avatar" }, props.value ? initials(props.value) : "DEF"),
      h("span", { className: "la-profile-name" }, props.label),
      props.selected ? h("span", { className: "la-check", "aria-hidden": "true" }, "✓") : null
    );
  }

  function RuleRow(props) {
    var row = props.row;
    var unknown = row.profile && props.profiles.indexOf(row.profile) === -1;
    return h(
      "div",
      { className: cn("la-rule", unknown && "la-rule-bad") },
      h("code", { className: "la-chip", title: row.label }, row.label),
      h("span", { className: "la-arrow", "aria-hidden": "true" }, "→"),
      h(
        "select",
        {
          className: "la-select",
          value: row.profile,
          "aria-label": "Agent for " + row.label + ": " + (row.profile || "none"),
          onChange: function (e) {
            props.onChange(props.index, { profile: e.target.value });
          },
        },
        h("option", { value: "" }, "Choose agent"),
        unknown ? h("option", { value: row.profile }, row.profile + " (unknown)") : null,
        props.profiles.map(function (name) {
          return h("option", { key: name, value: name }, name);
        })
      ),
      h(
        Button,
        {
          type: "button",
          className: cn("la-button-quiet", "la-remove"),
          "aria-label": "Remove rule " + row.label,
          onClick: function () {
            props.onRemove(props.index);
          },
        },
        "×"
      ),
      unknown ? h("span", { className: "la-rule-error" }, "Unknown profile — pick another agent.") : null
    );
  }

  function ResolutionCard(props) {
    return h(
      "div",
      { className: "la-card la-resolution" },
      h("div", { className: "la-micro" }, "Resolution order"),
      h("ol", { className: "la-resolution-list" },
        h("li", null, "A matching label rule", h("span", null, "its agent")),
        h("li", null, "A label named after a profile", h("span", null, "that profile")),
        h("li", null, "Everything else", h("span", null, props.active || "Gateway default"))
      )
    );
  }

  function ActionBar(props) {
    var tone = props.tone || "idle";
    return h(
      "div",
      { className: cn("la-actionbar", "la-actionbar-" + tone) },
      h(
        "span",
        { className: "la-actionbar-dot", "aria-hidden": "true" },
        tone === "ok" ? "✓" : tone === "bad" ? "!" : "•"
      ),
      h(
        "div",
        { className: "la-actionbar-msg", role: "status", "aria-live": "polite" },
        props.message,
        props.detail ? h("code", { className: "la-actionbar-detail" }, props.detail) : null
      ),
      h(
        "div",
        { className: "la-actionbar-buttons" },
        props.onDiscard && props.dirty
          ? h(Button, { type: "button", className: cn("la-button-quiet"), onClick: props.onDiscard }, "Discard")
          : null,
        props.onSave
          ? h(
              Button,
              {
                type: "button",
                className: cn("la-button-primary"),
                disabled: props.disabled,
                onClick: props.onSave,
              },
              props.saving
                ? "Saving…"
                : props.failed
                ? "Try again"
                : "Save changes"
            )
          : null
      )
    );
  }

  // ---------------------------------------------------------------- page
  function AgentPage() {
    var state = useState({ loading: true, error: "", saved: null });
    var cur = state[0];
    var setState = state[1];

    var draftState = useState({ agent_profile: "", routing: [] });
    var draft = draftState[0];
    var setDraft = draftState[1];

    var flags = useState({ saving: false, failed: false, status: "", detail: "" });
    var f = flags[0];
    var setFlags = flags[1];

    var ui = useState({ query: "", showAll: false, newLabel: "", newProfile: "", ruleError: "", savedFlash: false });
    var u = ui[0];
    var patchUi = ui[1];

    function setUi(changes) {
      patchUi(function (prev) {
        return Object.assign({}, prev, changes);
      });
    }

    function hydrate(doc) {
      var map = doc.routing || {};
      var rows = Object.keys(map)
        .sort()
        .map(function (label) {
          return { label: label, profile: map[label] };
        });
      setDraft({ agent_profile: doc.active || "", routing: rows });
      return doc;
    }

    var load = useCallback(function () {
      setState(function (prev) {
        return { loading: true, error: "", saved: prev.saved };
      });
      if (typeof SDK.fetchJSON !== "function") {
        setState({
          loading: false,
          saved: null,
          error:
            "This dashboard does not expose SDK.fetchJSON, so the plugin cannot authenticate its API calls. Update Hermes.",
        });
        return;
      }
      Promise.resolve(SDK.fetchJSON(API + "/state"))
        .then(function (doc) {
          setState({ loading: false, error: "", saved: hydrate(doc) });
        })
        .catch(function (err) {
          setState({ loading: false, saved: null, error: messageOf(err) });
        });
    }, []);

    useEffect(function () {
      load();
    }, [load]);

    var saved = cur.saved || {};
    var profiles = (saved.profiles || []).filter(Boolean);
    // "default" is the gateway default under another name; exactly one gateway entry.
    var realProfiles = profiles.filter(function (p) {
      return String(p).toLowerCase() !== "default";
    });
    var envAgent = !!saved.env_override;
    var envRouting = !!(saved.env_routing || saved.routing_from_env);
    var locked = envAgent;

    var savedRouting = {};
    Object.keys(saved.routing || {}).forEach(function (k) {
      savedRouting[k] = saved.routing[k];
    });
    var draftRouting = {};
    draft.routing.forEach(function (row) {
      var label = String(row.label || "").trim().toLowerCase();
      if (label && row.profile) draftRouting[label] = row.profile;
    });

    var dirtyProfile = (draft.agent_profile || "") !== (saved.active || "");
    var dirtyRouting = JSON.stringify(draftRouting) !== JSON.stringify(savedRouting);
    var dirty = dirtyProfile || dirtyRouting;

    var visibleProfiles = useMemo(
      function () {
        var q = (u.query || "").trim().toLowerCase();
        if (!q) return realProfiles;
        return realProfiles.filter(function (p) {
          return p.toLowerCase().indexOf(q) !== -1;
        });
      },
      [realProfiles, u.query]
    );
    var filtering = !!(u.query || "").trim();
    var shown = filtering || u.showAll ? visibleProfiles : visibleProfiles.slice(0, PAGE_SIZE);

    var ruleTargets = realProfiles.filter(function (name) {
      return profiles.indexOf(name) !== -1;
    });

    // -------------------------------------------------------------- actions
    function addRule() {
      var label = (u.newLabel || "").trim().toLowerCase();
      if (!label) return;
      if (label.length > 50) {
        setUi({ ruleError: "Keep label names under 50 characters." });
        return;
      }
      var exists = draft.routing.some(function (row) {
        return String(row.label).toLowerCase() === label;
      });
      if (exists) {
        setUi({ ruleError: "“" + label + "” is already routed." });
        return;
      }
      if (!u.newProfile) {
        setUi({ ruleError: "Choose an agent for this label." });
        return;
      }
      setUi({ ruleError: "", newLabel: "", newProfile: "" });
      setDraft(function (prev) {
        return { agent_profile: prev.agent_profile, routing: prev.routing.concat([{ label: label, profile: u.newProfile }]) };
      });
    }

    function changeRule(index, changes) {
      setDraft(function (prev) {
        var rows = prev.routing.slice();
        rows[index] = Object.assign({}, rows[index], changes);
        return { agent_profile: prev.agent_profile, routing: rows };
      });
    }

    function removeRule(index) {
      setDraft(function (prev) {
        return { agent_profile: prev.agent_profile, routing: prev.routing.filter(function (_, i) { return i !== index; }) };
      });
    }

    function discard() {
      hydrate(saved);
      setFlags({ saving: false, failed: false, status: "", detail: "" });
      setUi({ savedFlash: false, ruleError: "" });
    }

    function save() {
      setFlags({ saving: true, failed: false, status: "", detail: "" });
      Promise.resolve(
        SDK.fetchJSON(API + "/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent_profile: draft.agent_profile || "", routing: draftRouting }),
        })
      )
        .then(function (doc) {
          var next = hydrate(doc);
          setState({ loading: false, error: "", saved: next });
          setFlags({ saving: false, failed: false, status: "", detail: "" });
          setUi({ savedFlash: true });
        })
        .catch(function (err) {
          setFlags({
            saving: false,
            failed: true,
            status: messageOf(err),
            detail: "POST /api/plugins/linear-agent/settings · " + (err && err.status ? err.status : ""),
          });
        });
    }

    // -------------------------------------------------------------- render
    if (cur.loading && !cur.saved) {
      return h(
        "div",
        { className: "la-page" },
        h("header", { className: "la-head" }, h("h1", { className: "la-title" }, "Linear agent")),
        h("p", { className: "la-muted" }, "Loading…")
      );
    }

    if (cur.error) {
      return h(
        "div",
        { className: "la-page" },
        h("header", { className: "la-head" }, h("h1", { className: "la-title" }, "Linear agent")),
        h(
          "div",
          { className: "la-card la-card-error" },
          h("p", { className: "la-error-title" }, "Could not load the Linear agent settings"),
          h("p", { className: "la-error-body" }, cur.error),
          h(Button, { type: "button", onClick: load }, "Retry")
        )
      );
    }

    var bridge = saved.bridge || {};
    var activeName = saved.active || "";
    var sentence = !activeName
      ? "No profile is pinned. The gateway decides which agent answers."
      : bridge.reachable
      ? "Every issue without a matching label rule is answered by " + activeName + "."
      : "When the bridge is back, issues without a label rule go to " + activeName + ".";

    var bannerList = [];
    if (!bridge.reachable) {
      bannerList.push(
        h(Banner, {
          key: "down",
          tone: "warn",
          title: "The Linear bridge is not running",
          body: "Your choice is saved, but Linear issues will not be answered until the container is back up. Nothing else needs to change.",
          detail: (bridge.url || "") + " · " + (bridge.error || "connection refused"),
          action: { label: "Retry", onClick: load },
        })
      );
    }
    if (envAgent) {
      bannerList.push(
        h(Banner, {
          key: "env-agent",
          tone: "warn",
          title: "This page cannot change the agent",
          body: "LINEAR_AGENT_PROFILE is set by the deployment and takes precedence over anything saved here.",
          detail: "LINEAR_AGENT_PROFILE=" + activeName,
        })
      );
    }
    if (envRouting) {
      bannerList.push(
        h(Banner, {
          key: "env-routing",
          tone: "warn",
          title: "Label rules come from the deployment",
          body: "LINEAR_AGENT_ROUTING is set by the deployment. Rules saved here are ignored until it is removed.",
        })
      );
    }

    var messages = {
      ok: "Saved · takes effect on the next issue, no restart.",
      saving: "Saving…",
      bad: "Could not save. Nothing was written. Your changes are still here.",
      dirty:
        (dirtyProfile ? draft.agent_profile || "Gateway default" : null)
          ? "Unsaved changes. " + (activeName || "Gateway default") + " is answering until you save."
          : "Unsaved changes.",
      idle: bridge.reachable
        ? "Changes apply to the next issue — no restart."
        : "No changes. Your saved choice applies as soon as the bridge is reachable.",
    };

    var barMessage = f.saving
      ? messages.saving
      : f.failed
      ? messages.bad
      : u.savedFlash && !dirty
      ? messages.ok
      : dirty
      ? messages.dirty
      : messages.idle;

    var ruleCount = draft.routing.length;

    return h(
      "div",
      { className: "la-page" },
      h(
        "header",
        { className: "la-head" },
        h(
          "div",
          null,
          h("h1", { className: "la-title" }, "Linear agent"),
          h("p", { className: "la-sub" }, "Choose which Hermes agent answers your Linear issues.")
        ),
        h(
          "div",
          { className: "la-head-right" },
          h(
            "span",
            { className: cn("la-pill", bridge.reachable ? "la-pill-ok" : "la-pill-warn"), title: bridge.url || "" },
            h("span", { className: cn("la-dot", bridge.reachable ? "la-dot-ok" : "la-dot-warn") }),
            bridge.reachable ? "Bridge reachable" : "Bridge unreachable",
            bridge.reachable && bridge.version ? h("span", { className: "la-pill-version" }, "v" + bridge.version) : null
          ),
          h(
            Button,
            { type: "button", className: cn("la-button-quiet", "la-icon"), "aria-label": bridge.reachable ? "Refresh bridge status" : "Retry bridge connection", onClick: load },
            "⟳"
          )
        )
      ),
      sdkMajor(SDK.sdkVersion) > SUPPORTED_SDK_MAJOR
        ? h("p", { className: "la-note la-note-warn" }, "This tab was built for plugin SDK v" + SUPPORTED_SDK_MAJOR + "; the dashboard reports v" + SDK.sdkVersion + ".")
        : null,
      bannerList,

      h(
        "div",
        { className: "la-columns" },
        h(
          "section",
          { className: "la-card la-agent" },
          h(
            "div",
            { className: "la-agent-summary" },
            h("span", { className: "la-avatar la-avatar-lg" }, activeName ? initials(activeName) : "DEF"),
            h(
              "div",
              { className: "la-agent-meta" },
              h("div", { className: "la-micro" }, "Answering Linear"),
              h("div", { className: "la-agent-name" }, activeName || "Gateway default"),
              h("span", { className: "la-badge" }, "Active"),
              h("p", { className: "la-para" }, sentence)
            ),
            h(
              "div",
              { className: "la-agent-side" },
              h("div", { className: "la-micro" }, "Label rules"),
              h("div", { className: "la-agent-side-value" }, ruleCount ? ruleCount + " configured" : "None configured")
            )
          ),
          h("div", { className: "la-divider" }),
          h(
            "div",
            { className: "la-picker" },
            h(
              "div",
              { className: "la-picker-head" },
              h("h2", { className: "la-section-title" }, "Choose a profile"),
              realProfiles.length > 8
                ? h(Input, {
                    className: cn("la-input la-filter"),
                    placeholder: "Filter profiles",
                    value: u.query,
                    onChange: function (e) {
                      setUi({ query: e.target.value });
                    },
                  })
                : null
            ),
            realProfiles.length === 0
              ? h(
                  "div",
                  { className: "la-empty" },
                  h("p", { className: "la-para" }, "No agent profiles on this host"),
                  h("p", { className: "la-note" }, "The bridge answers as the gateway default agent until one exists. Create a profile, then reload this page."),
                  h("code", { className: "la-chip" }, "hermes profile create cody")
                )
              : h(
                  "div",
                  { className: "la-grid", role: "group", "aria-label": "Choose a profile" },
                  h(ProfileCard, {
                    key: "__gateway__",
                    value: "",
                    label: "Gateway default",
                    selected: !draft.agent_profile,
                    disabled: locked,
                    onSelect: function () {
                      setDraft(function (prev) {
                        return { agent_profile: "", routing: prev.routing };
                      });
                      setUi({ savedFlash: false });
                    },
                  }),
                  shown.map(function (name) {
                    return h(ProfileCard, {
                      key: name,
                      value: name,
                      label: name,
                      selected: draft.agent_profile === name,
                      disabled: locked,
                      onSelect: function () {
                        setDraft(function (prev) {
                          return { agent_profile: name, routing: prev.routing };
                        });
                        setUi({ savedFlash: false });
                      },
                    });
                  })
                ),
            realProfiles.length === 0
              ? null
              : h(
                  "div",
                  { className: "la-picker-foot" },
                  h(
                    "span",
                    { className: "la-note" },
                    filtering
                      ? visibleProfiles.length === 1
                        ? "1 match"
                        : visibleProfiles.length + " matches"
                      : shown.length + " of " + (realProfiles.length + 1) + " shown"
                  ),
                  realProfiles.length > PAGE_SIZE && !filtering
                    ? h(
                        Button,
                        {
                          type: "button",
                          className: cn("la-button-quiet"),
                          onClick: function () {
                            setUi({ showAll: !u.showAll });
                          },
                        },
                        u.showAll ? "Show fewer profiles" : "Show all " + (realProfiles.length + 1) + " profiles"
                      )
                    : null
                ),
            filtering && visibleProfiles.length === 0
              ? h("p", { className: "la-note" }, "No profile matches “" + u.query + "”.")
              : null
          )
        ),

        h(
          "aside",
          { className: "la-side" },
          h(
            "section",
            { className: "la-card la-rules" },
            h(
              "div",
              { className: "la-rules-head" },
              h("h2", { className: "la-section-title" }, "Label rules"),
              h("span", { className: "la-badge la-badge-quiet" }, String(ruleCount)),
              h("p", { className: "la-note" }, "Route an issue by the labels it carries.")
            ),
            h(
              "div",
              { className: "la-rule-list" },
              ruleCount === 0
                ? h("p", { className: "la-note" }, "No label rules. Every issue goes to " + (draft.agent_profile || "Gateway default") + ".")
                : draft.routing.map(function (row, index) {
                    return h(RuleRow, {
                      key: label_key(row, index),
                      row: row,
                      index: index,
                      profiles: ruleTargets,
                      onChange: changeRule,
                      onRemove: removeRule,
                    });
                  })
            ),
            envRouting
              ? null
              : h(
                  "div",
                  { className: "la-addrule" },
                  h(Input, {
                    className: cn("la-input la-newlabel"),
                    placeholder: "label-name",
                    value: u.newLabel,
                    onChange: function (e) {
                      setUi({ newLabel: e.target.value, ruleError: "" });
                    },
                    onKeyDown: function (e) {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addRule();
                      }
                    },
                  }),
                  h("span", { className: "la-arrow", "aria-hidden": "true" }, "→"),
                  h(
                    "select",
                    {
                      className: "la-select",
                      value: u.newProfile,
                      onChange: function (e) {
                        setUi({ newProfile: e.target.value, ruleError: "" });
                      },
                    },
                    h("option", { value: "" }, "Choose agent"),
                    ruleTargets.map(function (name) {
                      return h("option", { key: name, value: name }, name);
                    })
                  ),
                  h(Button, { type: "button", className: cn("la-button"), onClick: addRule }, "Add")
                ),
            u.ruleError ? h("p", { className: "la-rule-error" }, u.ruleError) : null,
            h("p", { className: "la-note la-hint" }, "Labels match in any case · first match wins.")
          ),
          h(ResolutionCard, { active: activeName })
        )
      ),

      h(ActionBar, {
        tone: f.failed ? "bad" : u.savedFlash && !dirty ? "ok" : "idle",
        message: barMessage,
        detail: f.failed ? f.detail : "",
        dirty: dirty,
        saving: f.saving,
        failed: f.failed,
        disabled: !dirty || f.saving || envAgent || !!(draft.routing.some(function (r) { return r.profile && ruleTargets.indexOf(r.profile) === -1; })),
        onSave: save,
        onDiscard: discard,
      })
    );
  }

  function label_key(row, index) {
    return String(row.label || "") + ":" + index;
  }

  registry.register("linear-agent", AgentPage);
})();
