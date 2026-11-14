import { postLogin } from "../transport/auth";

/** Test-only fixture credentials; callers install MSW handlers before use. */
export function attemptLogin(usr = "rec", pwd = "fixture") {
  return postLogin(usr, pwd);
}
