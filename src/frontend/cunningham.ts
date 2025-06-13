import { cunninghamConfig as tokens } from '@gouvfr-lasuite/ui-kit';

// NOTE: Ideally this would contain the same values accross all of the apps 20250614:Alevale
const customColors = {
  // NOTE: This colours seem to be the ones that are really ruling most of the app theme 20250614:Alevale
  'primary-800': '#000091',
  'primary-text': '#000091',

};

// NOTE: This is a similar copy to what's in DOCS,
// although not everything is supported, so I'm
// commenting what doesn't work 20250614:Alevale
tokens.themes.default.theme = {
  ...tokens.themes.default.theme,
  ...{
    // logo: {
    //   src: '',
    //   alt: '',
    //   widthHeader: '',
    //   widthFooter: '',
    // },
    colors: {
      ...tokens.themes.default.theme.colors,
      ...customColors,
    },
  },
};

tokens.themes.default.components = {
  ...tokens.themes.default.components,
  ...{
    'la-gaufre': false,
    'home-proconnect': false,
    beta: false,
    footer: true,
    // 'image-system-filter': '',
    // favicon: {
    //   ico: '/assets/favicon-light.ico',
    //   'png-light': '/assets/favicon-light.png',
    //   'png-dark': '/assets/favicon-dark.png',
    // },
  },
};

const dsfrTheme = {
  dsfr: {
    theme: {
      colors: {
        'secondary-icon': '#C9191E',
      },
      // logo: {
      //   src: '/assets/logo-gouv.svg',
      //   widthHeader: '110px',
      //   widthFooter: '220px',
      //   alt: 'Gouvernement Logo',
      // },
    },
    components: {
      'la-gaufre': true,
      'home-proconnect': true,
      beta: true,
      footer: true,
      // favicon: {
      //   ico: '/assets/favicon-dsfr.ico',
      //   'png-light': '/assets/favicon-dsfr.png',
      //   'png-dark': '/assets/favicon-dark-dsfr.png',
      // },
    },
  },
};

// NOTE: Some more room for customization 20250614:Alevale
const genericTheme = {
  generic: {
    theme: {
      // logo: {
      //   src: '/images/app-icon.svg',
      //   widthHeader: '90px',
      //   widthFooter: '110px',
      //   alt: 'Some Logo',
      // },
      colors: {
        'primary-action': '#0443F2',
        'primary-focus': '#0443F2',
        'primary-text': '#0443F2',
        'primary-050': '#E6F0FF',
        'primary-100': '#CCE0FF',
        'primary-150': '#B3D1FF',
        'primary-200': '#99C2FF',
        'primary-300': '#66A3FF',
        'primary-400': '#3385FF',
        'primary-500': '#0443F2',
        'primary-600': '#0339CC',
        'primary-700': '#022EAA',
        'primary-800': '#022488',
        'primary-900': '#011966',
        'primary-950': '#011244',

        'secondary-text': '#262626',
        'secondary-50': '#F9F9F9',
        'secondary-100': '#F1F1F1',
        'secondary-200': '#E2E2E2',
        'secondary-300': '#CFCFCF',
        'secondary-400': '#B9B9B9',
        'secondary-500': '#9E9E9E',
        'secondary-600': '#7F7F7F',
        'secondary-700': '#5C5C5C',
        'secondary-800': '#3A3A3A',
        'secondary-900': '#262626',
        'secondary-950': '#1A1A1A',

        'greyscale-text': '#3C3B38',
        'greyscale-000': '#fff',
        'greyscale-050': '#F8F7F7',
        'greyscale-100': '#F3F3F2',
        'greyscale-200': '#ECEBEA',
        'greyscale-250': '#E4E3E2',
        'greyscale-300': '#D3D2CF',
        'greyscale-350': '#eee',
        'greyscale-400': '#96948E',
        'greyscale-500': '#817E77',
        'greyscale-600': '#6A6862',
        'greyscale-700': '#3C3B38',
        'greyscale-750': '#383632',
        'greyscale-800': '#2D2B27',
        'greyscale-900': '#262522',
        'greyscale-950': '#201F1C',
        'greyscale-1000': '#181714',

        'success-text': '#234935',
        'success-50': '#F3FBF5',
        'success-100': '#E4F7EA',
        'success-200': '#CAEED4',
        'success-300': '#A0E0B5',
        'success-400': '#6CC88C',
        'success-500': '#6CC88C',
        'success-600': '#358D5C',
        'success-700': '#2D704B',
        'success-800': '#28583F',
        'success-900': '#234935',
        'success-950': '#0F281B',

        'info-text': '#212445',
        'info-50': '#F2F6FB',
        'info-100': '#E2E9F5',
        'info-200': '#CCD8EE',
        'info-300': '#A9C0E3',
        'info-400': '#809DD4',
        'info-500': '#617BC7',
        'info-600': '#4A5CBF',
        'info-700': '#3E49B2',
        'info-800': '#353C8F',
        'info-900': '#303771',
        'info-950': '#212445',

        'warning-text': '#D97C3A',
        'warning-50': '#FDF7F1',
        'warning-100': '#FBEDDC',
        'warning-200': '#F5D9B9',
        'warning-300': '#EDBE8C',
        'warning-400': '#E2985C',
        'warning-500': '#D97C3A',
        'warning-600': '#C96330',
        'warning-700': '#A34B32',
        'warning-800': '#813B2C',
        'warning-900': '#693327',
        'warning-950': '#381713',

        'danger-action': '#C0182A',
        'danger-text': '#FFF',
        'danger-050': '#FDF5F4',
        'danger-100': '#FBEBE8',
        'danger-200': '#F9E0DC',
        'danger-300': '#F3C3BD',
        'danger-400': '#E26552',
        'danger-500': '#C91F00',
        'danger-600': '#A71901',
        'danger-700': '#562C2B',
        'danger-800': '#392425',
        'danger-900': '#311F20',
        'danger-950': '#2A191A',
        'blue-400': '#8BAECC',
        'blue-500': '#567AA2',
        'blue-600': '#455784',
        'brown-400': '#E4C090',
        'brown-500': '#BA9977',
        'brown-600': '#735C45',
        'cyan-400': '#5CBEC9',
        'cyan-500': '#43A1B3',
        'cyan-600': '#39809B',
        'gold-400': '#ECBF50',
        'gold-500': '#DFA038',
        'gold-600': '#C17B31',
        'green-400': '#5DBD9A',
        'green-500': '#3AA183',
        'green-600': '#2A816D',
        'olive-400': '#AFD662',
        'olive-500': '#90BB4B',
        'olive-600': '#6E9441',
        'orange-400': '#E2985C',
        'orange-500': '#D97C3A',
        'orange-600': '#C96330',
        'pink-400': '#BE8FC8',
        'pink-500': '#A563B1',
        'pink-600': '#8B44A5',
        'purple-400': '#BE8FC8',
        'purple-500': '#A563B1',
        'purple-600': '#8B44A5',
        'yellow-400': '#EDC947',
        'yellow-500': '#DBB13A',
        'yellow-600': '#B88A34',
      },
      font: {
        families: {
          base: 'Inter, Roboto Flex Variable, sans-serif',
          accent: 'Inter, Roboto Flex Variable, sans-serif',
        },
      },
    },
    components: {
      'la-gaufre': false,
      'home-proconnect': false,
      beta: true,
      footer: true,
      button: {
        primary: {
          background: {
            'color-hover': 'var(--c--theme--colors--primary-focus)',
            'color-active': 'var(--c--theme--colors--primary-focus)',
            'color-focus': 'var(--c--theme--colors--primary-focus)',
          },
        },
      },
      // 'image-system-filter': 'saturate(0.5)',
    },
  },
};

const docsTokens = {
  ...tokens,
  themes: {
    ...tokens.themes,
    ...dsfrTheme,
    ...genericTheme,
  },
};

export default docsTokens;
