import os
import sys
import uuid

from leapp.cli.commands import command_utils
from leapp.cli.commands.config import get_config
from leapp.cli.commands.upgrade import breadcrumbs, util
from leapp.exceptions import CommandError, LeappError
from leapp.logger import configure_logger
from leapp.utils.audit import Execution
from leapp.utils.clicmd import command, command_opt
from leapp.utils.output import beautify_actor_exception, report_errors, report_info


@command('preupgrade', help='Generate preupgrade report')
@command_opt('whitelist-experimental', action='append', metavar='ActorName', help='Enables experimental actors')
@command_opt('enable-experimental-feature', action='append', metavar='Feature',
             help=('Enable experimental feature. '
                   'Available experimental features: {}').format(util.get_help_str_with_avail_experimental_features()),
             choices=list(util.EXPERIMENTAL_FEATURES), default=[])
@command_opt('debug', is_flag=True, help='Enable debug mode', inherit=False)
@command_opt('verbose', is_flag=True, help='Enable verbose logging', inherit=False)
@command_opt('no-rhsm', is_flag=True, help='Use only custom repositories and skip actions'
                                           ' with Red Hat Subscription Manager')
@command_opt('no-insights-register', is_flag=True, help='Do not register into Red Hat Insights')
@command_opt('no-rhsm-facts', is_flag=True, help='Do not store migration information using Red Hat '
                                                 'Subscription Manager. Automatically implied by --no-rhsm.')
@command_opt('enablerepo', action='append', metavar='<repoid>',
             help='Enable specified repository. Can be used multiple times.')
@command_opt('channel',
             help='Set preferred channel for the IPU target.',
             choices=['ga', 'e4s', 'eus', 'aus'],
             value_type=str.lower)  # This allows the choices to be case insensitive
@command_opt('iso', help='Use provided target RHEL installation image to perform the in-place upgrade.')
@command_opt('target', help='Specify RHEL version to upgrade to for {} detected upgrade flavour'.format(
                 command_utils.get_upgrade_flavour()))
@command_opt('report-schema', help='Specify report schema version for leapp-report.json',
             choices=['1.0.0', '1.1.0', '1.2.0'], default=get_config().get('report', 'schema'))
@command_opt('nogpgcheck', is_flag=True, help='Disable RPM GPG checks. Same as yum/dnf --nogpgcheck option.')
@breadcrumbs.produces_breadcrumbs
def preupgrade(args, breadcrumbs):
    util.disable_database_sync()
    context = str(uuid.uuid4())
    cfg = get_config()
    util.handle_output_level(args)
    configuration = util.prepare_configuration(args)
    answerfile_path = cfg.get('report', 'answerfile')
    userchoices_path = cfg.get('report', 'userchoices')
    # NOTE(ivasilev) argparse choices and defaults in enough for validation
    report_schema = args.report_schema

    if os.getuid():
        raise CommandError('This command has to be run under the root user.')
    e = Execution(context=context, kind='preupgrade', configuration=configuration)
    e.store()
    util.archive_logfiles()
    logger = configure_logger('leapp-preupgrade.log')
    os.environ['LEAPP_EXECUTION_ID'] = context

    try:
        repositories = util.load_repositories()
    except LeappError as exc:
        raise CommandError(exc.message)

    command_utils.set_resource_limits()

    workflow = repositories.lookup_workflow('IPUWorkflow')()

    command_utils.load_actor_configs_and_store_it_in_db(context, repositories, cfg)

    util.warn_if_unsupported(configuration)
    util.process_whitelist_experimental(repositories, workflow, configuration, logger)
    with beautify_actor_exception():
        workflow.load_answers(answerfile_path, userchoices_path)
        until_phase = 'ReportsPhase'
        logger.info('Executing workflow until phase: %s', until_phase)

        # Set the locale, so that the actors parsing command outputs that might be localized will not fail
        os.environ['LANGUAGE'] = 'en_US.UTF-8'
        os.environ['LC_ALL'] = 'en_US.UTF-8'
        os.environ['LANG'] = 'en_US.UTF-8'
        workflow.run(context=context, until_phase=until_phase, skip_dialogs=True)

    logger.info("Answerfile will be created at %s", answerfile_path)
    workflow.save_answers(answerfile_path, userchoices_path)
    util.generate_report_files(context, report_schema)
    report_errors(workflow.errors)
    report_files = util.get_cfg_files('report', cfg)
    log_files = util.get_cfg_files('logs', cfg)
    report_info(context, report_files, log_files, answerfile_path, fail=workflow.failure, errors=workflow.errors)

    if workflow.failure:
        sys.exit(1)


def register(base_command):
    """
        Registers `leapp preupgrade`
    """
    base_command.add_sub(preupgrade)
