// The definition of a workflow (rapo/jobs/workflow.py) as the editor holds it, and back.
//
// In the form the steps are the names of the jobs, in order; the definition has them as objects ({job}), so that a
// step can get settings later. Parameters are those of a job (utils/job.js).

import { MASK, conditionToForm, emptyCondition, formToCondition, targetProblems } from "./job";

// `condition` carries `on`, the switch, as in a job.
export function emptyWorkflowForm() {
  return { steps: [], parameters: [], timeout: null, overlap: "skip", condition: { on: false, ...emptyCondition(), kind: "RUN_STATUS" } };
}

export function definitionToForm(definition) {
  const form = emptyWorkflowForm();
  form.steps = (definition.steps || []).map((step) => step.job);
  form.parameters = (definition.parameters || []).map((item) => ({ name: item.name, value: item.value || "", secret: Boolean(item.secret) }));
  form.timeout = definition.timeout ?? null;
  form.overlap = definition.overlap || "skip";
  if (definition.condition) {
    form.condition = { on: true, ...conditionToForm(definition.condition) };
  }
  return form;
}

export function formToDefinition(form) {
  const definition = { version: 1, steps: form.steps.map((job) => ({ job })) };
  definition.parameters = form.parameters.filter((item) => item.name.trim() !== "").map((item) => ({ name: item.name.trim(), value: item.value, secret: item.secret }));
  if (form.timeout !== null && form.timeout !== "") {
    definition.timeout = Number(form.timeout);
  }
  definition.overlap = form.overlap;
  if (form.condition.on) {
    definition.condition = formToCondition(form.condition);
  }
  return definition;
}

// "1 and 3", "1, 3 and 5": the steps a job is at, counted from 1.
function stepsText(numbers) {
  return numbers.length === 2 ? `steps ${numbers[0]} and ${numbers[1]}` : `steps ${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
}

// {job: [step numbers]} of the jobs that are in more than one step.
export function repeatedJobs(steps) {
  const places = {};
  steps.forEach((job, index) => {
    (places[job] = places[job] || []).push(index + 1);
  });
  return Object.fromEntries(Object.entries(places).filter(([, numbers]) => numbers.length > 1));
}

// What is wrong with the form that can be seen without the server, in the words of the server; [] when nothing.
export function formProblems(form) {
  const problems = [];
  if (!form.steps.length) {
    problems.push("A workflow needs at least one step: add a job.");
  }
  for (const [job, numbers] of Object.entries(repeatedJobs(form.steps))) {
    problems.push(`The job ${job} is repeated, at ${stepsText(numbers)}: a job can be in a workflow once.`);
  }
  if (form.condition.on && form.condition.kind === "RUN_STATUS") {
    problems.push(...targetProblems(form.condition.targets, "Condition of the workflow"));
  }
  const names = form.parameters.map((item) => item.name.trim()).filter(Boolean);
  const twice = names.find((name, index) => names.indexOf(name) !== index);
  if (twice) {
    problems.push(`Parameter ${twice} is given twice.`);
  }
  return problems;
}

export { MASK };
