// The definition of a job (rapo/jobs/definition.py) as the editor holds it, and back.
//
// A task is an object with the settings of its type. In the form the lists are text, one entry per line, so that they
// can be typed: the arguments of a program, the Name:value arguments of a transfer, and NAME=value for the environment.

export const SCRIPT_TYPES = ["PYTHON", "PERL", "SHELL", "BATCH", "POWERSHELL"];
// The types that may be the task of a condition of kind EXIT_CODE (they return an exit code).
export const EXIT_CODE_TYPES = ["EXECUTABLE", "COMMAND", ...SCRIPT_TYPES];

export const OVERLAPS = [
  { value: "skip", label: "Skip", caption: "A start while the job is still running is not run: a manual one is refused, a scheduled one is recorded as canceled" },
  { value: "queue", label: "Queue", caption: "A start while the job is still running waits for it to end" },
  { value: "allow", label: "Allow", caption: "The runs of the job go side by side" },
];

export const CONDITION_KINDS = [
  { value: "SQL", label: "SQL query", caption: "A query that returns one value: met when it is not empty and not 0" },
  { value: "EXIT_CODE", label: "Exit code", caption: "A program, command or script: met when it exits with 0" },
  { value: "FILE_EXISTS", label: "File exists", caption: "A file or folder exists, or does not" },
  { value: "FILE_COUNT", label: "Files in a folder", caption: "The number of files of a folder is within limits" },
  { value: "RUN_STATUS", label: "Run status", caption: "Other runs (a control, a transfer, a job or a workflow) have ended well" },
];

// The kinds a workflow may start with: the ones that need no process.
export const WORKFLOW_CONDITION_KINDS = CONDITION_KINDS.filter((item) => ["RUN_STATUS", "FILE_EXISTS", "FILE_COUNT"].includes(item.value));

// What a condition of kind RUN_STATUS can wait for, and the windows a wait can look at.
export const TARGET_TYPES = [
  { value: "control", label: "Control", icon: "fas fa-tasks" },
  { value: "transfer", label: "Transfer", icon: "fas fa-exchange-alt" },
  { value: "job", label: "Job", icon: "fas fa-cog" },
  { value: "workflow", label: "Workflow", icon: "fas fa-project-diagram" },
];
export const TARGET_WINDOWS = [
  { value: "today", label: "Ended today" },
  { value: "hours", label: "Ended in the last hours" },
  { value: "since_last_run", label: "Ended since the last successful run of this one" },
  { value: "data_date", label: "Has the data of a day" },
];

export function emptyTarget(type = "control") {
  return { type, name: "", window: "today", hours: 24, days_ago: 0 };
}

// A target as the definition has it: {type, name, window: {kind, hours | days_ago}}.
export function targetToForm(target) {
  const window = target.window || { kind: "today" };
  return { type: target.type, name: target.name, window: window.kind, hours: window.hours ?? 24, days_ago: window.days_ago ?? 0 };
}

export function formToTarget(form) {
  const window = { kind: form.window };
  if (form.window === "hours") {
    window.hours = Number(form.hours);
  } else if (form.window === "data_date") {
    window.days_ago = Number(form.days_ago);
  }
  return { type: form.type, name: String(form.name || "").trim(), window };
}

// Say in words what a target waits for ("control X: ended today"), for the lists.
export function targetText(target) {
  const window = target.window || { kind: "today" };
  const text = {
    today: "ended today",
    hours: `ended in the last ${window.hours} hours`,
    since_last_run: "ended since the last successful run",
    data_date: window.days_ago ? `has the data of ${window.days_ago} day${window.days_ago === 1 ? "" : "s"} ago` : "has the data of today",
  }[window.kind];
  return `${target.type} ${target.name}: ${text}`;
}

// What the server sends instead of the value of a secret parameter.
export const MASK = "********";

// The shells a SHELL and a POWERSHELL task may pick.
export const SHELLS = { SHELL: ["sh", "bash"], POWERSHELL: ["powershell", "pwsh"] };

const lines = (text) =>
  String(text || "")
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "");

export function emptyTask(type = "PYTHON") {
  return { type, text: "", param: "", arguments_text: "", path: "", command: "", source: "code", file: "", code: "", args_text: "", cwd: "", env_text: "", interpreter: "", shell: "" };
}

export function parseEnv(text) {
  const env = {};
  const bad = [];
  for (const line of lines(text)) {
    const at = line.indexOf("=");
    if (at <= 0) {
      bad.push(line);
    } else {
      env[line.slice(0, at).trim()] = line.slice(at + 1);
    }
  }
  return { env, bad };
}

// A task of the definition as the form holds it.
export function taskToForm(task) {
  const form = emptyTask(task.type);
  form.text = task.text || "";
  form.param = task.param || "";
  form.arguments_text = (task.arguments || []).join("\n");
  form.path = task.path || "";
  form.command = task.command || "";
  form.source = task.file ? "file" : "code";
  form.file = task.file || "";
  form.code = task.code || "";
  form.args_text = (task.args || []).join("\n");
  form.cwd = task.cwd || "";
  form.env_text = Object.entries(task.env || {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  form.interpreter = task.interpreter || "";
  form.shell = task.shell || "";
  return form;
}

// The task of the definition from the form: only what the type has, and only what is filled in.
export function formToTask(form) {
  const task = { type: form.type };
  const setList = (key, text) => lines(text).length && (task[key] = lines(text));
  const common = () => {
    setList("args", form.args_text);
    if (form.cwd.trim()) {
      task.cwd = form.cwd.trim();
    }
    const { env } = parseEnv(form.env_text);
    if (Object.keys(env).length) {
      task.env = env;
    }
  };
  if (form.type === "SQL") {
    task.text = form.text;
  } else if (form.type === "TRANSFER") {
    task.param = form.param.trim();
    setList("arguments", form.arguments_text);
  } else if (form.type === "EXECUTABLE") {
    task.path = form.path.trim();
    common();
  } else if (form.type === "COMMAND") {
    task.command = form.command;
    if (form.cwd.trim()) {
      task.cwd = form.cwd.trim();
    }
    const { env } = parseEnv(form.env_text);
    if (Object.keys(env).length) {
      task.env = env;
    }
  } else {
    if (form.source === "file") {
      task.file = form.file.trim();
    } else {
      task.code = form.code;
    }
    common();
    if (["PYTHON", "PERL"].includes(form.type) && form.interpreter.trim()) {
      task.interpreter = form.interpreter.trim();
    }
    if (SHELLS[form.type] && form.shell) {
      task.shell = form.shell;
    }
  }
  return task;
}

// What is wrong with a task before it is sent, as the texts the server would not say as well; [] when nothing.
export function taskProblems(form, label) {
  const problems = [];
  const { bad } = parseEnv(form.env_text);
  if (bad.length) {
    problems.push(`${label}: the environment line "${bad[0]}" is not NAME=value.`);
  }
  if (form.type === "TRANSFER") {
    const wrong = lines(form.arguments_text).find((line) => !line.includes(":") || line.indexOf(":") === 0);
    if (wrong) {
      problems.push(`${label}: the argument "${wrong}" is not Name:value.`);
    }
  }
  return problems;
}

export function emptyCondition() {
  return { kind: "SQL", text: "", task: emptyTask("COMMAND"), path: "", exists: true, directory: "", mask: "", min: null, max: null, targets: [emptyTarget()] };
}

export function conditionToForm(condition) {
  const form = emptyCondition();
  form.kind = condition.kind;
  form.text = condition.text || "";
  if (condition.task) {
    form.task = taskToForm(condition.task);
  }
  form.path = condition.path || "";
  form.exists = condition.exists !== false;
  form.directory = condition.directory || "";
  form.mask = condition.mask || "";
  form.min = condition.min ?? null;
  form.max = condition.max ?? null;
  if (condition.targets) {
    form.targets = condition.targets.map(targetToForm);
  }
  return form;
}

export function formToCondition(form) {
  const condition = { kind: form.kind };
  if (form.kind === "SQL") {
    condition.text = form.text;
  } else if (form.kind === "EXIT_CODE") {
    condition.task = formToTask(form.task);
  } else if (form.kind === "RUN_STATUS") {
    condition.targets = form.targets.map(formToTarget);
  } else if (form.kind === "FILE_EXISTS") {
    condition.path = form.path.trim();
    condition.exists = form.exists;
  } else {
    condition.directory = form.directory.trim();
    if (form.mask.trim()) {
      condition.mask = form.mask.trim();
    }
    for (const key of ["min", "max"]) {
      if (form[key] !== null && form[key] !== "") {
        condition[key] = Number(form[key]);
      }
    }
  }
  return condition;
}

// The editor's form of a definition: `pre`, `condition` and `post` carry `on`, the switch of the part.
export function emptyJobForm(type = "PYTHON") {
  return {
    pre: { on: false, task: emptyTask("SQL") },
    condition: { on: false, ...emptyCondition() },
    main: emptyTask(type),
    post: { on: false, task: emptyTask("SQL") },
    parameters: [],
    timeout: null,
    overlap: "skip",
  };
}

export function definitionToForm(definition) {
  const form = emptyJobForm(definition.main ? definition.main.type : undefined);
  for (const part of ["pre", "post"]) {
    if (definition[part]) {
      form[part] = { on: true, task: taskToForm(definition[part]) };
    }
  }
  if (definition.condition) {
    form.condition = { on: true, ...conditionToForm(definition.condition) };
  }
  if (definition.main) {
    form.main = taskToForm(definition.main);
  }
  form.parameters = (definition.parameters || []).map((item) => ({ name: item.name, value: item.value || "", secret: Boolean(item.secret) }));
  form.timeout = definition.timeout ?? null;
  form.overlap = definition.overlap || "skip";
  return form;
}

export function formToDefinition(form) {
  const definition = { version: 1 };
  if (form.pre.on) {
    definition.pre = formToTask(form.pre.task);
  }
  if (form.condition.on) {
    definition.condition = formToCondition(form.condition);
  }
  definition.main = formToTask(form.main);
  if (form.post.on) {
    definition.post = formToTask(form.post.task);
  }
  definition.parameters = form.parameters.filter((item) => item.name.trim() !== "").map((item) => ({ name: item.name.trim(), value: item.value, secret: item.secret }));
  if (form.timeout !== null && form.timeout !== "") {
    definition.timeout = Number(form.timeout);
  }
  definition.overlap = form.overlap;
  return definition;
}

// What is wrong with the targets of a condition of kind RUN_STATUS that can be seen without the server.
export function targetProblems(targets, label = "Condition") {
  const problems = [];
  if (!targets.length) {
    problems.push(`${label}: give at least one target.`);
  }
  const seen = new Set();
  targets.forEach((target, index) => {
    const name = String(target.name || "").trim();
    if (!name) {
      problems.push(`${label}: target ${index + 1} has no name.`);
    } else if (seen.has(`${target.type} ${name}`)) {
      problems.push(`${label}: the ${target.type} ${name} is given twice.`);
    }
    seen.add(`${target.type} ${name}`);
    if (target.window === "hours" && !(Number(target.hours) >= 1 && Number(target.hours) <= 720)) {
      problems.push(`${label}: target ${index + 1}: hours is 1 to 720.`);
    }
    if (target.window === "data_date" && !(Number(target.days_ago) >= 0 && Number(target.days_ago) <= 31)) {
      problems.push(`${label}: target ${index + 1}: days back is 0 to 31.`);
    }
  });
  return problems;
}

// The problems of the form that can be seen without the server.
export function formProblems(form) {
  const problems = [...taskProblems(form.main, "Main")];
  if (form.pre.on) {
    problems.push(...taskProblems(form.pre.task, "Pre"));
  }
  if (form.post.on) {
    problems.push(...taskProblems(form.post.task, "Post"));
  }
  if (form.condition.on && form.condition.kind === "EXIT_CODE") {
    problems.push(...taskProblems(form.condition.task, "Condition"));
  }
  if (form.condition.on && form.condition.kind === "RUN_STATUS") {
    problems.push(...targetProblems(form.condition.targets));
  }
  const names = form.parameters.map((item) => item.name.trim()).filter(Boolean);
  const twice = names.find((name, index) => names.indexOf(name) !== index);
  if (twice) {
    problems.push(`Parameter ${twice} is given twice.`);
  }
  return problems;
}

// The units of a schedule object (ScheduleEditBox) as save-job and save-job-schedule take them.
export function scheduleToUnits(schedule) {
  const days = (value) => (value && value.length !== 0 ? String(value) : null);
  const time = (value) => (value != null ? String(value) : null);
  return { mday: days(schedule.mday), wday: days(schedule.wday), hour: time(schedule.hour), min: time(schedule.min), sec: time(schedule.sec) };
}

// Whether a form has SQL in it, which only a token with Free SQL may save.
export function formUsesSql(form) {
  return form.main.type === "SQL" || (form.pre.on && form.pre.task.type === "SQL") || (form.post.on && form.post.task.type === "SQL") || (form.condition.on && form.condition.kind === "SQL");
}
