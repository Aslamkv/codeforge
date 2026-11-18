lazy import time

from ..memory.memories import Scratchpad
from ..messages import say
from .code import BugReproducer, CodeWriter, TestToolSetup, TestWriter, Verifier
from .finish import FinalReview, LessonWriter, ReadmeWriter, Tidy
from .plan import Designer, InterfaceReviewer, Recaller, SpecWriter
from .project import Steps, Tracker
from .session import Stopped

PHASE_CLASSES = {
    phase.NAME: phase
    for phase in (
        Recaller,
        SpecWriter,
        Designer,
        InterfaceReviewer,
        TestToolSetup,
        TestWriter,
        CodeWriter,
        Verifier,
        Tidy,
        FinalReview,
        ReadmeWriter,
        LessonWriter,
        BugReproducer,
    )
}


class CycleRunner:
    def __init__(self, session):
        self.session, self.project = session, session.project
        self.stopped = None

    def run(self, cycle, web=False):
        steps = Steps(cycle.phases, self.session.tracker)
        self.session.cycle = cycle
        if web and cycle.phase not in ("recall", "done") and not cycle.awaiting:
            self.recall_first(cycle, steps)
        went = steps.run(
            cycle.phase,
            lambda phase: self.phase_done(phase, cycle),
            lambda phase: self.reached(phase, cycle),
            waits=lambda: cycle.awaiting,
        )
        if went == "waiting":
            return "awaiting"
        Scratchpad.write({"chat": cycle.chat, "task": cycle.request, "status": went})
        return went

    def phase_done(self, phase, cycle):
        worker = PHASE_CLASSES[phase](self.session)
        if self.run_phase(worker, phase, cycle) is False:
            self.disprove(cycle, phase)
            return False
        if worker.confirms and cycle.confirm and not cycle.confirmed:
            cycle["awaiting"] = True
        worker.after(cycle)
        return True

    def reached(self, phase, cycle):
        cycle["phase"] = phase
        self.session.save()

    def recall_first(self, cycle, steps):
        steps.enter("recall")
        with say.part(f"{Recaller.TITLE} (with --web, before {cycle.phase})", Tracker.icon("recall")):
            Recaller(self.session).run(cycle)

    def run_phase(self, worker, phase, cycle):
        if not worker.TITLE:
            return self.result(worker.run(cycle))
        started = time.perf_counter()
        with say.part(worker.title(cycle), Tracker.icon(phase)):
            went = self.result(worker.run(cycle))
            if went is not False:
                say.took(Tracker.done_words(phase), time.perf_counter() - started)
        if self.session.tracker is not None:
            self.session.tracker.outcome(worker.outcome(cycle))
        return went

    def result(self, went):
        if isinstance(went, Stopped):
            self.stopped = went
            return False
        return went

    def disprove(self, cycle, phase):
        gone = self.session.knowledge.disprove(cycle.used, self.session.failed_phases(phase, self.stopped))
        if gone:
            say.info(f"{gone} principle(s) used in this step are discarded: they did not hold", "🗑️")
        self.session.save()
