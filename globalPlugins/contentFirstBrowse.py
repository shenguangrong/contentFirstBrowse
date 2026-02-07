# NVDA 插件：contentFirstBrowse
# 在浏览模式的光标移动中强制“内容优先”的朗读顺序（适用于所有角色）。

import builtins
import itertools
import globalPluginHandler
import browseMode
import speech
import speech.speech as speechMod
from controlTypes import OutputReason
from logHandler import log

_original_getTextInfoSpeech = speechMod.getTextInfoSpeech
_isPatched = False


def _t(text):
    translate = getattr(builtins, "_", None)
    return translate(text) if callable(translate) else text


def _isBrowseModeTextInfo(info):
    return isinstance(info, browseMode.BrowseModeDocumentTextInfo)


def _shouldApplyContentFirstForTextInfo(info):
    try:
        doc = info.obj
        if getattr(doc, "passThrough", False):
            return False
        lastMoveWasFocus = getattr(doc, "_lastCaretMoveWasFocus", None)
        if lastMoveWasFocus is None:
            return False
        return not lastMoveWasFocus
    except Exception:
        return False


# 基于 NVDA 2025.3 的 speech.getTextInfoSpeech，实现浏览模式光标移动的内容优先顺序。
def _patched_getTextInfoSpeech(
    info,
    useCache=True,
    formatConfig=None,
    unit=None,
    reason=OutputReason.QUERY,
    _prefixSpeechCommand=None,
    onlyInitialFields=False,
    suppressBlanks=False,
):
    if (
        reason != OutputReason.CARET
        or not _isBrowseModeTextInfo(info)
        or not _shouldApplyContentFirstForTextInfo(info)
    ):
        result = yield from _original_getTextInfoSpeech(
            info,
            useCache,
            formatConfig,
            unit,
            reason,
            _prefixSpeechCommand,
            onlyInitialFields,
            suppressBlanks,
        )
        return result

    textInfos = speechMod.textInfos
    controlTypes = speechMod.controlTypes
    config = speechMod.config
    languageHandling = speechMod.languageHandling
    ReportLineIndentation = speechMod.ReportLineIndentation
    unicodeNormalize = speechMod.unicodeNormalize
    LangChangeCommand = speechMod.LangChangeCommand
    EndUtteranceCommand = speechMod.EndUtteranceCommand
    isBlank = speechMod.isBlank
    splitTextIndentation = speechMod.splitTextIndentation
    getIndentationSpeech = speechMod.getIndentationSpeech
    _extendSpeechSequence_addMathForTextInfo = speechMod._extendSpeechSequence_addMathForTextInfo
    _getTextInfoSpeech_considerSpelling = speechMod._getTextInfoSpeech_considerSpelling
    _getTextInfoSpeech_updateCache = speechMod._getTextInfoSpeech_updateCache
    SpeakTextInfoState = speechMod.SpeakTextInfoState

    if isinstance(useCache, SpeakTextInfoState):
        speakTextInfoState = useCache
    elif useCache:
        speakTextInfoState = SpeakTextInfoState(info.obj)
    else:
        speakTextInfoState = None
    extraDetail = unit in (textInfos.UNIT_CHARACTER, textInfos.UNIT_WORD)
    if not formatConfig:
        formatConfig = config.conf["documentFormatting"]
    formatConfig = formatConfig.copy()
    if extraDetail:
        formatConfig["extraDetail"] = True
    reportIndentation = (
        unit == textInfos.UNIT_LINE and formatConfig["reportLineIndentation"] != ReportLineIndentation.OFF
    )
    # For performance reasons, when navigating by paragraph or table cell, spelling errors will not be announced.
    if unit in (textInfos.UNIT_PARAGRAPH, textInfos.UNIT_CELL) and reason == OutputReason.CARET:
        formatConfig["reportSpellingErrors"] = False

    # Fetch the last controlFieldStack, or make a blank one
    controlFieldStackCache = speakTextInfoState.controlFieldStackCache if speakTextInfoState else []
    formatFieldAttributesCache = speakTextInfoState.formatFieldAttributesCache if speakTextInfoState else {}
    textWithFields = info.getTextWithFields(formatConfig)
    # We don't care about node bounds, especially when comparing fields.
    # Remove them.
    for command in textWithFields:
        if not isinstance(command, textInfos.FieldCommand):
            continue
        field = command.field
        if not field:
            continue
        try:
            del field["_startOfNode"]
        except KeyError:
            pass
        try:
            del field["_endOfNode"]
        except KeyError:
            pass

    # Make a new controlFieldStack and formatField from the textInfo's initialFields
    newControlFieldStack = []
    newFormatField = textInfos.FormatField()
    initialFields = []
    for field in textWithFields:
        if isinstance(field, textInfos.FieldCommand) and field.command in ("controlStart", "formatChange"):
            initialFields.append(field.field)
        else:
            break
    if len(initialFields) > 0:
        del textWithFields[0 : len(initialFields)]
    endFieldCount = 0
    for field in reversed(textWithFields):
        if isinstance(field, textInfos.FieldCommand) and field.command == "controlEnd":
            endFieldCount += 1
        else:
            break
    if endFieldCount > 0:
        del textWithFields[0 - endFieldCount :]
    for field in initialFields:
        if isinstance(field, textInfos.ControlField):
            newControlFieldStack.append(field)
        elif isinstance(field, textInfos.FormatField):
            newFormatField.update(field)
        else:
            raise ValueError("unknown field: %s" % field)
    # Calculate how many fields in the old and new controlFieldStacks are the same
    commonFieldCount = 0
    for count in range(min(len(newControlFieldStack), len(controlFieldStackCache))):
        # #2199: When comparing controlFields try using uniqueID if it exists before resorting to compairing the entire dictionary
        oldUniqueID = controlFieldStackCache[count].get("uniqueID")
        newUniqueID = newControlFieldStack[count].get("uniqueID")
        if ((oldUniqueID is not None or newUniqueID is not None) and newUniqueID == oldUniqueID) or (
            newControlFieldStack[count] == controlFieldStackCache[count]
        ):
            commonFieldCount += 1
        else:
            break

    speechSequence = []
    # #2591: Only if the reason is not focus, Speak the exit of any controlFields not in the new stack.
    # We don't do this for focus because hearing "out of list", etc. isn't useful when tabbing or using quick navigation and makes navigation less efficient.
    if reason not in [OutputReason.FOCUS, OutputReason.QUICKNAV]:
        endingBlock = False
        for count in reversed(range(commonFieldCount, len(controlFieldStackCache))):
            fieldSequence = info.getControlFieldSpeech(
                controlFieldStackCache[count],
                controlFieldStackCache[0:count],
                "end_removedFromControlFieldStack",
                formatConfig,
                extraDetail,
                reason=reason,
            )
            if fieldSequence:
                speechSequence.extend(fieldSequence)
            if not endingBlock and reason == OutputReason.SAYALL:
                endingBlock = bool(int(controlFieldStackCache[count].get("isBlock", 0)))
        if endingBlock:
            speechSequence.append(EndUtteranceCommand())
    # The TextInfo should be considered blank if we are only exiting fields (i.e. we aren't
    # entering any new fields and there is no text).
    shouldConsiderTextInfoBlank = True

    if _prefixSpeechCommand is not None:
        assert isinstance(_prefixSpeechCommand, speechMod.SpeechCommand)
        speechSequence.append(_prefixSpeechCommand)

    # 收集控制字段的开始序列，稍后在内容之后朗读。
    pendingStartFields = []

    # Get speech text for any fields that are in both controlFieldStacks, if extra detail is not requested
    if not extraDetail:
        for count in range(commonFieldCount):
            field = newControlFieldStack[count]
            fieldSequence = info.getControlFieldSpeech(
                field,
                newControlFieldStack[0:count],
                "start_inControlFieldStack",
                formatConfig,
                extraDetail,
                reason=reason,
            )
            hasMath = field.get("role") == controlTypes.Role.MATH
            if fieldSequence or hasMath:
                shouldConsiderTextInfoBlank = False
                pendingStartFields.append(
                    {
                        "field": field,
                        "sequence": fieldSequence,
                        "flushed": False,
                        "hasMath": hasMath,
                    }
                )

    # When true, we are inside a clickable field, and should therefore not announce any more new clickable fields
    inClickable = False
    # Get speech text for any fields in the new controlFieldStack that are not in the old controlFieldStack
    for count in range(commonFieldCount, len(newControlFieldStack)):
        field = newControlFieldStack[count]
        fieldSequence = []
        if not inClickable and formatConfig["reportClickable"]:
            states = field.get("states")
            if states and controlTypes.State.CLICKABLE in states:
                # We entered the most outer clickable, so announce it, if we won't be announcing anything else interesting for this field
                presCat = field.getPresentationCategory(newControlFieldStack[0:count], formatConfig, reason)
                if not presCat or presCat is field.PRESCAT_LAYOUT:
                    fieldSequence.append(controlTypes.State.CLICKABLE.displayString)
                    shouldConsiderTextInfoBlank = False
                inClickable = True
        fieldSequence.extend(
            info.getControlFieldSpeech(
                field,
                newControlFieldStack[0:count],
                "start_addedToControlFieldStack",
                formatConfig,
                extraDetail,
                reason=reason,
            )
        )
        hasMath = field.get("role") == controlTypes.Role.MATH
        if fieldSequence or hasMath:
            shouldConsiderTextInfoBlank = False
            pendingStartFields.append(
                {
                    "field": field,
                    "sequence": fieldSequence,
                    "flushed": False,
                    "hasMath": hasMath,
                }
            )
        commonFieldCount += 1

    # Fetch the text for format field attributes that have changed between what was previously cached, and this textInfo's initialFormatField.
    fieldSequence = info.getFormatFieldSpeech(
        newFormatField,
        formatFieldAttributesCache,
        formatConfig,
        reason=reason,
        unit=unit,
        extraDetail=extraDetail,
        initialFormat=True,
    )
    if fieldSequence:
        speechSequence.extend(fieldSequence)
    language = None
    lastLanguage = None
    if languageHandling.shouldMakeLangChangeCommand():
        language = newFormatField.get("language")
        speechSequence.append(LangChangeCommand(language))
        lastLanguage = language
    isWordOrCharUnit = unit in (textInfos.UNIT_CHARACTER, textInfos.UNIT_WORD)
    firstText = ""
    if len(textWithFields) > 0:
        firstField = textWithFields[0]
        if isinstance(firstField, str):
            firstText = firstField.strip() if not firstField.isspace() else firstField
    if onlyInitialFields or (
        isWordOrCharUnit
        and (len(firstText) == 1 or len(unicodeNormalize(firstText)) == 1)
        and all(speechMod._isControlEndFieldCommand(x) for x in itertools.islice(textWithFields, 1, None))
    ):
        if reason != OutputReason.ONLYCACHE:
            yield from _getTextInfoSpeech_considerSpelling(
                unit,
                onlyInitialFields,
                textWithFields,
                reason,
                speechSequence,
                language,
            )
        if useCache:
            _getTextInfoSpeech_updateCache(
                useCache,
                speakTextInfoState,
                newControlFieldStack,
                formatFieldAttributesCache,
            )
        return False

    # Similar to before, but If the most inner clickable is exited, then we allow announcing clickable for the next lot of clickable fields entered.
    inClickable = False
    # Move through the field commands, getting speech text for all controlStarts, controlEnds and formatChange commands
    # But also keep newControlFieldStack up to date as we will need it for the ends
    # Add any text to a separate list, as it must be handled differently.
    # Also make sure that LangChangeCommand objects are added before any controlField or formatField speech
    relativeSpeechSequence = []
    inTextChunk = False
    allIndentation = ""
    indentationDone = False

    def _appendPendingSequence(seq):
        nonlocal lastLanguage, relativeSpeechSequence
        if not seq:
            return
        restoreLang = None
        if languageHandling.shouldMakeLangChangeCommand() and lastLanguage is not None:
            restoreLang = lastLanguage
            relativeSpeechSequence.append(LangChangeCommand(None))
            lastLanguage = None
        relativeSpeechSequence.extend(seq)
        if languageHandling.shouldMakeLangChangeCommand() and restoreLang is not None:
            relativeSpeechSequence.append(LangChangeCommand(restoreLang))
            lastLanguage = restoreLang

    def _flushPending():
        nonlocal inTextChunk
        for pending in pendingStartFields:
            if pending["flushed"]:
                continue
            seq = pending["sequence"]
            hasMath = pending["hasMath"]
            if not seq and not hasMath:
                pending["flushed"] = True
                continue
            inTextChunk = False
            _appendPendingSequence(seq)
            if hasMath:
                _extendSpeechSequence_addMathForTextInfo(relativeSpeechSequence, info, pending["field"])
            pending["flushed"] = True

    for command in textWithFields:
        if isinstance(command, str):
            # Text should break a run of clickables
            inClickable = False
            if reportIndentation and not indentationDone:
                indentation, command = splitTextIndentation(command)
                # Combine all indentation into one string for later processing.
                allIndentation += indentation
                if command:
                    # There was content after the indentation, so there is no more indentation.
                    indentationDone = True
            if command:
                if inTextChunk:
                    relativeSpeechSequence[-1] += command
                else:
                    relativeSpeechSequence.append(command)
                    inTextChunk = True
                if not isBlank(command):
                    _flushPending()
        elif isinstance(command, textInfos.FieldCommand):
            newLanguage = None
            deferredControlStart = False
            if command.command == "controlStart":
                # Control fields always start a new chunk, even if they have no field text.
                inTextChunk = False
                fieldSequence = []
                if not inClickable and formatConfig["reportClickable"]:
                    states = command.field.get("states")
                    if states and controlTypes.State.CLICKABLE in states:
                        # We have entered an outer most clickable or entered a new clickable after exiting a previous one
                        # Announce it if there is nothing else interesting about the field, but not if the user turned it off.
                        presCat = command.field.getPresentationCategory(
                            newControlFieldStack[0:],
                            formatConfig,
                            reason,
                        )
                        if not presCat or presCat is command.field.PRESCAT_LAYOUT:
                            fieldSequence.append(controlTypes.State.CLICKABLE.displayString)
                        inClickable = True
                fieldSequence.extend(
                    info.getControlFieldSpeech(
                        command.field,
                        newControlFieldStack,
                        "start_relative",
                        formatConfig,
                        extraDetail,
                        reason=reason,
                    )
                )
                newControlFieldStack.append(command.field)
                hasMath = command.field.get("role") == controlTypes.Role.MATH
                if fieldSequence or hasMath:
                    pendingStartFields.append(
                        {
                            "field": command.field,
                            "sequence": fieldSequence,
                            "flushed": False,
                            "hasMath": hasMath,
                        }
                    )
                    fieldSequence = []
                    deferredControlStart = True
            elif command.command == "controlEnd":
                # Exiting a controlField should break a run of clickables
                inClickable = False
                # Control fields always start a new chunk, even if they have no field text.
                inTextChunk = False
                fieldSequence = info.getControlFieldSpeech(
                    newControlFieldStack[-1],
                    newControlFieldStack[0:-1],
                    "end_relative",
                    formatConfig,
                    extraDetail,
                    reason=reason,
                )
                pending = None
                if pendingStartFields and pendingStartFields[-1]["field"] is newControlFieldStack[-1]:
                    pending = pendingStartFields.pop()
                if pending and not pending["flushed"]:
                    combined = []
                    if pending["sequence"]:
                        combined.extend(pending["sequence"])
                    if pending["hasMath"]:
                        _extendSpeechSequence_addMathForTextInfo(combined, info, pending["field"])
                    combined.extend(fieldSequence)
                    fieldSequence = combined
                    pending["flushed"] = True
                del newControlFieldStack[-1]
                if commonFieldCount > len(newControlFieldStack):
                    commonFieldCount = len(newControlFieldStack)
            elif command.command == "formatChange":
                fieldSequence = info.getFormatFieldSpeech(
                    command.field,
                    formatFieldAttributesCache,
                    formatConfig,
                    reason=reason,
                    unit=unit,
                    extraDetail=extraDetail,
                )
                if fieldSequence:
                    inTextChunk = False
                if languageHandling.shouldMakeLangChangeCommand():
                    newLanguage = command.field.get("language")
                    if lastLanguage != newLanguage:
                        # The language has changed, so this starts a new text chunk.
                        inTextChunk = False
            if not inTextChunk:
                if fieldSequence:
                    if languageHandling.shouldMakeLangChangeCommand() and lastLanguage is not None:
                        # Fields must be spoken in the default language.
                        relativeSpeechSequence.append(LangChangeCommand(None))
                        lastLanguage = None
                    relativeSpeechSequence.extend(fieldSequence)
                if (
                    command.command == "controlStart"
                    and not deferredControlStart
                    and command.field.get("role") == controlTypes.Role.MATH
                ):
                    _extendSpeechSequence_addMathForTextInfo(relativeSpeechSequence, info, command.field)
                if languageHandling.shouldMakeLangChangeCommand() and newLanguage != lastLanguage:
                    relativeSpeechSequence.append(LangChangeCommand(newLanguage))
                    lastLanguage = newLanguage

    # 冲刷所有尚未遇到文本的待处理 control start 序列。
    _flushPending()

    if (
        reportIndentation
        and speakTextInfoState
        and (
            # either not ignoring blank lines
            not formatConfig["ignoreBlankLinesForRLI"]
            # or line isn't completely blank
            or any(not (set(t) <= speechMod.LINE_END_CHARS) for t in textWithFields if isinstance(t, str))
        )
        and allIndentation != speakTextInfoState.indentationCache
    ):
        indentationSpeech = getIndentationSpeech(allIndentation, formatConfig)
        if languageHandling.shouldMakeLangChangeCommand() and speechSequence[-1].lang is not None:
            # Indentation must be spoken in the default language,
            # but the initial format field specified a different language.
            # Insert the indentation before the LangChangeCommand.
            langChange = speechSequence.pop()
            speechSequence.extend(indentationSpeech)
            speechSequence.append(langChange)
        else:
            speechSequence.extend(indentationSpeech)
        if speakTextInfoState:
            speakTextInfoState.indentationCache = allIndentation
    # Don't add this text if it is blank.
    relativeBlank = True
    for x in relativeSpeechSequence:
        if isinstance(x, str) and not isBlank(x):
            relativeBlank = False
            break
    if not relativeBlank:
        speechSequence.extend(relativeSpeechSequence)
        shouldConsiderTextInfoBlank = False

    # Finally get speech text for any fields left in new controlFieldStack that are common with the old controlFieldStack (for closing), if extra detail is not requested
    if languageHandling.shouldMakeLangChangeCommand() and lastLanguage is not None:
        speechSequence.append(
            LangChangeCommand(None),
        )
        lastLanguage = None
    if not extraDetail:
        for count in reversed(range(min(len(newControlFieldStack), commonFieldCount))):
            fieldSequence = info.getControlFieldSpeech(
                newControlFieldStack[count],
                newControlFieldStack[0:count],
                "end_inControlFieldStack",
                formatConfig,
                extraDetail,
                reason=reason,
            )
            if fieldSequence:
                speechSequence.extend(fieldSequence)
                shouldConsiderTextInfoBlank = False

    # If there is nothing that should cause the TextInfo to be considered
    # non-blank, blank should be reported, unless we are doing a say all.
    if not suppressBlanks and reason != OutputReason.SAYALL and shouldConsiderTextInfoBlank:
        # Translators: This is spoken when the line is considered blank.
        speechSequence.append(_t("blank"))

    # Cache a copy of the new controlFieldStack for future use
    if useCache:
        _getTextInfoSpeech_updateCache(
            useCache,
            speakTextInfoState,
            newControlFieldStack,
            formatFieldAttributesCache,
        )

    if reason == OutputReason.ONLYCACHE or not speechSequence:
        return False

    yield speechSequence
    return True


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super().__init__()
        global _isPatched
        if not _isPatched:
            speechMod.getTextInfoSpeech = _patched_getTextInfoSpeech
            speech.getTextInfoSpeech = _patched_getTextInfoSpeech
            _isPatched = True
            log.info("contentFirstBrowse: patched getTextInfoSpeech for browse mode caret")

    def terminate(self):
        global _isPatched
        if _isPatched:
            if speechMod.getTextInfoSpeech is _patched_getTextInfoSpeech:
                speechMod.getTextInfoSpeech = _original_getTextInfoSpeech
            if speech.getTextInfoSpeech is _patched_getTextInfoSpeech:
                speech.getTextInfoSpeech = _original_getTextInfoSpeech
            _isPatched = False
