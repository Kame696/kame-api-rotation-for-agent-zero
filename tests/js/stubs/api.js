// the host's callJsonApi: every call is recorded, the answer is whatever the test put in __responses
globalThis.__calls = globalThis.__calls || [];
globalThis.__responses = globalThis.__responses || {};
export async function callJsonApi(path, body) {
  globalThis.__calls.push([path, body]);
  const answer = globalThis.__responses[path];
  return typeof answer === "function" ? answer(body) : answer;
}
