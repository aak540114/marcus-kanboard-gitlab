<?php

namespace Kanboard\Plugin\MarcusDevEnv;

use Kanboard\Core\Plugin\Base;

/**
 * MarcusDevEnv — Kanboard plugin
 *
 * Adds a "View Live Changes" sidebar button to every task, plus a row of
 * Marcus controls (active-agents badge, project description link, gate
 * toggle, AI-verify counter, max-dev-envs counter) injected via the
 * project header, which is shared by every project-scoped view (board,
 * list, calendar, Gantt, search) — so it isn't limited to the board.
 *
 * Configuration
 * -------------
 * Set the environment variable MARCUS_URL to the base URL of your Marcus
 * MCP server (e.g. http://localhost:4298). Falls back to that default.
 */
class Plugin extends Base
{
    /**
     * Called by Kanboard when the plugin is loaded.
     */
    public function initialize(): void
    {
        $this->template->hook->attach(
            'template:task:sidebar:information',
            'MarcusDevEnv:task/sidebar'
        );
        // 'template:board:private:header' does not exist in Kanboard (verified
        // against app/Template/board/view_private.php and table_container.php,
        // both on master and the v1.2.52 release tag actually shipped by the
        // kanboard/kanboard:latest Docker image — neither fires any hook near
        // the board header). 'template:project:header:after' is the real hook
        // fired at the end of app/Template/project_header/header.php, which
        // every project-scoped view renders — this is what actually reaches
        // the page.
        $this->template->hook->attach(
            'template:project:header:after',
            'MarcusDevEnv:board/header'
        );
    }

    /**
     * Plugin metadata shown in the Kanboard plugin manager.
     */
    public function getPluginName(): string
    {
        return 'Marcus Dev Environment';
    }

    public function getPluginDescription(): string
    {
        return 'Adds a "View Live Changes" button to each task that spins up a hot-reload dev environment via Marcus.';
    }

    public function getPluginAuthor(): string
    {
        return 'Marcus';
    }

    public function getPluginVersion(): string
    {
        return '1.0.0';
    }

    public function getPluginHomepage(): string
    {
        return 'https://github.com/aak540114/marcus';
    }
}
