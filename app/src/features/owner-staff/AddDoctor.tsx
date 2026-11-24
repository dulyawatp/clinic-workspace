import { type FormEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button, Card } from "../../design/components";
import { captureClinicRequestContext } from "../../transport/native/frappe";
import { lookupNativeOwnerDoctorFinish } from "../../transport/native/owner-doctor-finish";
import {
  createNativeOwnerDoctorProfile,
  lookupNativeOwnerDoctorProfiles,
  type OwnerDoctorProfile,
  type OwnerDoctorProfileOptions,
  type OwnerDoctorProfileResult,
} from "../../transport/native/owner-doctor-profile";
import { DraftWorkingHours } from "./DraftWorkingHours";
import { DoctorFinish, type BlockedFinishReview, type FinishReview } from "./DoctorFinish";
import { DoctorScheduleReuse } from "./DoctorScheduleReuse";

type DraftProfile = OwnerDoctorProfile & { departmentDisplayName?: string };
type ProfileWrite = Exclude<OwnerDoctorProfileResult["kind"], "created"> | "idle" | "pending";

function choiceLabel({ name, displayName }: { name: string; displayName: string }) {
  return name === displayName ? displayName : `${displayName} — ${name}`;
}

export function AddDoctor({ onFinished }: { onFinished: () => void }) {
  const { t } = useTranslation();
  const region = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const feedback = useRef<HTMLParagraphElement>(null);
  const launched = useRef(false);
  const writing = useRef(false);
  const hoursWriting = useRef(false);
  const finishBlocks = useRef(new Map<string, BlockedFinishReview>());
  const [hoursPending, setHoursPending] = useState(false);
  const [lookup, setLookup] = useState<{
    options: OwnerDoctorProfileOptions;
    isCurrent: () => boolean;
    signal: AbortSignal;
  } | null>(null);
  const [revision, setRevision] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [existing, setExisting] = useState("");
  const [profile, setProfile] = useState<DraftProfile | null>(null);
  const [checking, setChecking] = useState<"resume" | "saved" | null>(null);
  const [review, setReview] = useState<FinishReview | null>(null);
  const [finishBlock, setFinishBlock] = useState<BlockedFinishReview | null>(null);
  const [choosingReuse, setChoosingReuse] = useState(false);
  const [finished, setFinished] = useState(false);
  const [write, setWrite] = useState<ProfileWrite>("idle");
  const step = !open ? "closed" : finished ? "finished" : review || checking === "saved" ? "review" : profile ? "hours" : "profile";
  const pending = write === "pending";
  const createLocked = write !== "idle" && write !== "validation";

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const isCurrent = captureClinicRequestContext();
    void lookupNativeOwnerDoctorProfiles(signal).catch(() => null).then((options) => {
      if (signal.aborted || !isCurrent()) return;
      setRefreshing(false);
      if (options) {
        setLookup({ options, signal, isCurrent });
      } else {
        // If availability can't be reconfirmed, leave the independent hours tool in place.
        setLookup(null);
        setOpen(false);
      }
    });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    if (!checking || !profile || !lookup?.isCurrent()) return;
    const controller = new AbortController();
    void lookupNativeOwnerDoctorFinish(profile.name, controller.signal).then((result) => {
      if (controller.signal.aborted || lookup.signal.aborted || !lookup.isCurrent()) return;
      if (result.kind === "ready" || checking === "saved") setReview(result);
      setChecking(null);
    });
    return () => controller.abort();
  }, [checking, profile, lookup]);

  useLayoutEffect(() => {
    if (step !== "closed") heading.current?.focus();
    else if (launched.current) region.current?.querySelector("button")?.focus();
  }, [step]);

  useLayoutEffect(() => {
    if (write !== "idle" && write !== "pending") feedback.current?.focus();
  }, [write]);

  function refreshProfiles() {
    setExisting("");
    setRefreshing(true);
    setRevision((value) => value + 1);
  }

  function openFlow() {
    if (!lookup?.isCurrent() || refreshing || hoursWriting.current) return;
    launched.current = true;
    setMode(!lookup.options.practitioners.length && canCreate ? "new" : "existing");
    setOpen(true);
  }

  function closeFlow() {
    if (writing.current || hoursWriting.current) return;
    if (profile) refreshProfiles();
    setProfile(null);
    setReview(null);
    setFinishBlock(null);
    setChoosingReuse(false);
    setChecking(null);
    setFinished(false);
    setExisting("");
    if (write === "validation") setWrite("idle");
    setOpen(false);
  }

  function continueExisting(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!lookup?.isCurrent() || lookup.signal.aborted || refreshing || writing.current) return;
    const selected = lookup.options.practitioners.find(({ name }) => name === existing);
    if (selected) {
      setProfile(selected);
      const blocked = finishBlocks.current.get(selected.name);
      if (blocked) {
        setFinishBlock(blocked);
        setReview(blocked);
      } else setChecking("resume");
    }
  }

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!lookup?.isCurrent() || lookup.signal.aborted || refreshing || writing.current || !canCreate) return;
    const fields = new FormData(event.currentTarget);
    const firstName = String(fields.get("firstName") ?? "").trim();
    const lastName = String(fields.get("lastName") ?? "").trim();
    const department = lookup.options.departments.find(({ name }) => name === fields.get("department"));
    if (!firstName || !department) return;

    writing.current = true;
    setWrite("pending");
    const result = await createNativeOwnerDoctorProfile({ firstName, lastName, department: department.name });
    writing.current = false;
    if (lookup.signal.aborted) return;
    if (!lookup.isCurrent()) {
      setWrite("uncertain");
      return;
    }
    if (result.kind === "created") {
      setWrite("idle");
      setProfile({ ...result.profile, departmentDisplayName: department.displayName });
    } else {
      setWrite(result.kind);
    }
  }

  function trackHoursPending(value: boolean) {
    hoursWriting.current = value;
    setHoursPending(value);
  }

  const options = lookup?.options;
  const error = write !== "idle" && write !== "pending" ? write : null;
  const canCreate = options?.canCreate !== false && Boolean(options?.departments.length) && !createLocked;
  const displayNameCount = new Map<string, number>();
  for (const { displayName } of options?.practitioners ?? []) {
    displayNameCount.set(displayName, (displayNameCount.get(displayName) ?? 0) + 1);
  }

  return (
    <>
      {options && (
        <div ref={region} data-testid="owner-doctor">
          <Card
            className="owner-doctor-card"
            title={t("ownerDoctor.title")}
            hint={t("ownerDoctor.intro")}
            actions={open ? (
              <Button onClick={closeFlow} disabled={pending || hoursPending} data-testid="owner-doctor-close">
                {t("ownerDoctor.close")}
              </Button>
            ) : (
              <Button
                variant="primary"
                aria-disabled={hoursPending || refreshing}
                onClick={openFlow}
                data-testid="owner-doctor-start"
              >
                {t("ownerDoctor.title")}
              </Button>
            )}
          >
            {open ? (
              <div className="owner-doctor-flow" data-testid="owner-doctor-flow">
                <ol className="owner-doctor-steps" aria-label={t("ownerDoctor.steps")}>
                  <li aria-current={step === "profile" ? "step" : undefined}>{t("ownerDoctor.profile")}</li>
                  <li aria-current={step === "hours" ? "step" : undefined}>{t("ownerDoctor.hours")}</li>
                  <li aria-current={step === "review" || step === "finished" ? "step" : undefined}>{t("ownerDoctor.review")}</li>
                </ol>
                <div className="owner-doctor-step">
                  <h3 ref={heading} tabIndex={-1}>
                    {t(`ownerDoctor.${step}`)}
                  </h3>
                  {profile ? !finished && (
                    <div className="owner-doctor-summary" data-testid="owner-doctor-profile-summary">
                      <div>
                        <strong><bdi>{profile.displayName}</bdi></strong>
                        {profile.departmentDisplayName && <p>{t("ownerDoctor.department")}: <bdi>{profile.departmentDisplayName}</bdi></p>}
                      </div>
                       {!finishBlock && <Badge tone="neutral">{t("ownerDoctor.draft")}</Badge>}
                    </div>
                  ) : (
                    <>
                      <div className="owner-doctor-actions" role="group" aria-label={t("ownerDoctor.profileMethod")}>
                        <Button
                          aria-pressed={mode === "existing"}
                          disabled={pending || refreshing}
                          onClick={() => setMode("existing")}
                          data-testid="owner-doctor-existing-mode"
                        >{t("ownerDoctor.useExisting")}</Button>
                        <Button
                          aria-pressed={mode === "new"}
                          disabled={refreshing || !canCreate}
                          onClick={() => setMode("new")}
                          data-testid="owner-doctor-new-mode"
                        >{t("ownerDoctor.createNew")}</Button>
                      </div>
                      {mode === "existing" ? (
                        options.practitioners.length ? (
                          <form className="owner-doctor-form" onSubmit={continueExisting} data-testid="owner-doctor-existing-form">
                            <label className="pc-field">
                              <span className="pc-field__label">{t("ownerDoctor.existingLabel")}</span>
                              <select required disabled={refreshing} value={existing} onChange={(event) => setExisting(event.target.value)} data-testid="owner-doctor-existing">
                                <option value="">{t("ownerDoctor.chooseProfile")}</option>
                                {options.practitioners.map((option) => (
                                    <option key={option.name} value={option.name}>
                                      {(displayNameCount.get(option.displayName) ?? 0) > 1
                                        ? choiceLabel(option)
                                        : option.displayName}
                                    </option>
                                ))}
                              </select>
                            </label>
                            <Button variant="primary" type="submit" disabled={refreshing || !existing} data-testid="owner-doctor-continue">
                              {t("ownerDoctor.continue")}
                            </Button>
                          </form>
                        ) : !refreshing && <p role="status">{t("ownerDoctor.noProfiles")}</p>
                      ) : options.departments.length ? (
                        <form className="owner-doctor-form" onSubmit={(event) => void createProfile(event)} data-testid="owner-doctor-create-form">
                          <fieldset className="owner-staff-add-fields" disabled={createLocked || refreshing} aria-label={t("ownerDoctor.createNew")}>
                            <div className="owner-staff-add-identity">
                              <label className="pc-field">
                                <span className="pc-field__label">{t("ownerStaff.add.firstName")}</span>
                                <input name="firstName" autoComplete="given-name" required pattern={".*\\S.*"} data-testid="owner-doctor-first-name" />
                              </label>
                              <label className="pc-field">
                                <span className="pc-field__label">{t("ownerDoctor.lastName")}</span>
                                <input name="lastName" autoComplete="family-name" data-testid="owner-doctor-last-name" />
                              </label>
                              <label className="pc-field">
                                <span className="pc-field__label">{t("ownerDoctor.department")}</span>
                                <select name="department" required defaultValue="" data-testid="owner-doctor-department">
                                  <option value="">{t("ownerDoctor.chooseDepartment")}</option>
                                  {options.departments.map((option) => (
                                    <option key={option.name} value={option.name}>{option.displayName}</option>
                                  ))}
                                </select>
                              </label>
                            </div>
                            <Button variant="primary" type="submit" className="owner-doctor-submit" data-testid="owner-doctor-create">
                              {t(pending ? "ownerDoctor.saving" : "ownerDoctor.saveProfile")}
                            </Button>
                          </fieldset>
                        </form>
                      ) : <p role="status">{t("ownerDoctor.noDepartments")}</p>}
                      {mode === "existing" && options.canCreate === false && <p className="pc-note">{t("ownerDoctor.denied")}</p>}
                      {mode === "existing" && options.canCreate !== false && !options.departments.length && <p className="pc-note">{t("ownerDoctor.noDepartments")}</p>}
                      {pending && <p role="status">{t("ownerDoctor.saving")}</p>}
                      {refreshing && <p role="status">{t("ownerDoctor.loading")}</p>}
                      {error && (
                        <p ref={feedback} tabIndex={-1} role="alert" className="owner-staff-edit-alert" data-testid={`owner-doctor-${error}`}>
                          {t(`ownerDoctor.${error}`)}
                        </p>
                      )}
                      {write === "uncertain" && (
                        <Button disabled={refreshing} onClick={() => {
                          setMode("existing");
                          refreshProfiles();
                        }} data-testid="owner-doctor-check-drafts">{t("ownerDoctor.checkDrafts")}</Button>
                      )}
                    </>
                  )}
                  {checking && <p role="status" data-testid="owner-doctor-finish-loading">{t("ownerDoctor.finish.loading")}</p>}
                  {step === "hours" && profile && !checking && (
                    <DoctorScheduleReuse
                      key={profile.name}
                      practitioner={profile.name}
                      disabled={hoursPending}
                      choosing={choosingReuse}
                      onChoosingChange={(choosing) => {
                        if (!hoursWriting.current) setChoosingReuse(choosing);
                      }}
                      onReviewed={(result) => {
                        setChoosingReuse(false);
                        setReview(result);
                      }}
                    />
                  )}
                  {profile && review && lookup && (
                    <DoctorFinish
                      key={profile.name}
                      practitioner={profile.name}
                      displayName={profile.displayName}
                      initialReview={review}
                      onChangeReuse={() => setReview(null)}
                      onPendingChange={trackHoursPending}
                      onBlocked={(block) => {
                        setFinishBlock(block);
                        if (block) finishBlocks.current.set(profile.name, block);
                        else finishBlocks.current.delete(profile.name);
                      }}
                       onFinished={() => {
                         setFinished(true);
                         onFinished();
                       }}
                    />
                  )}
                </div>
                {!finished && <p className="owner-doctor-safety" data-testid="owner-doctor-safety">{t("ownerDoctor.safety")}</p>}
              </div>
            ) : (
              <p className="pc-note">{t("ownerDoctor.safety")}</p>
            )}
          </Card>
        </div>
      )}
      {step === "hours" && profile && !checking && (
        <DraftWorkingHours
          key={profile.name}
          profile={profile}
          hidden={choosingReuse}
          onPendingChange={trackHoursPending}
          onSaved={() => setChecking("saved")}
        />
      )}
      <DraftWorkingHours hidden={open} onPendingChange={trackHoursPending} />
    </>
  );
}
