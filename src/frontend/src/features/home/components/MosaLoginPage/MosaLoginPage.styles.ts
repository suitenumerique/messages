import styled, { css, keyframes } from 'styled-components';

export const APP_COLOR = '#F8497B';
export const APP_GRADIENT_END = '#A0033A';

export const fadeIn = keyframes`
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
`;

export const pulse = keyframes`
  0%, 100% {
    transform: translate(-50%, -50%) scale(1);
  }
  50% {
    transform: translate(-50%, -50%) scale(1.1);
  }
`;

export const LoginContainer = styled.div`
  --font-heading: 'Poppins', sans-serif;
  --font-body: 'Open Sans', sans-serif;
  --app-color: ${APP_COLOR};
  --app-gradient-end: ${APP_GRADIENT_END};
  min-height: 100vh;
  font-family: var(--font-body);
  display: grid;
  grid-template-columns: 1fr 1fr;

  @media (max-width: 900px) {
    grid-template-columns: 1fr;
  }
`;

export const BrandPanel = styled.div`
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 3rem;
  overflow: hidden;

  @media (max-width: 900px) {
    display: none;
  }
`;

export const BrandBg = styled.div`
  position: absolute;
  inset: 0;
`;

export const GradientBase = styled.div`
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
`;

export const GridOverlay = styled.div`
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.13) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.13) 1px, transparent 1px);
  background-size: 64px 64px;
  background-position: left center;
  mask-image: radial-gradient(
    ellipse 68% 65% at 0% 50%,
    rgba(0, 0, 0, 1) 0%,
    rgba(0, 0, 0, 0) 100%
  );
  -webkit-mask-image: radial-gradient(
    ellipse 68% 65% at 0% 50%,
    rgba(0, 0, 0, 1) 0%,
    rgba(0, 0, 0, 0) 100%
  );
`;

interface AccentDotProps {
  $left: string;
  $top: string;
  $size: string;
  $opacity: number;
  $delay?: string;
}

export const AccentDot = styled.div<AccentDotProps>`
  position: absolute;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: rgba(255, 255, 255, ${(props: AccentDotProps) => props.$opacity});
  left: ${(props: AccentDotProps) => props.$left};
  top: ${(props: AccentDotProps) => props.$top};
  width: ${(props: AccentDotProps) => props.$size};
  height: ${(props: AccentDotProps) => props.$size};
  animation: ${pulse} 6s ease-in-out infinite;
  ${(props: AccentDotProps) =>
    props.$delay &&
    css`
      animation-delay: ${props.$delay};
    `}

  @media (prefers-reduced-motion: reduce) {
    animation: none;
  }
`;

export const BrandContent = styled.div`
  position: relative;
  text-align: center;
  color: white;
  z-index: 1;
  animation: ${fadeIn} 0.6s ease-out;

  img {
    display: block;
    width: 32rem;
    max-width: 80%;
    height: auto;
    margin: 0 auto;
  }

  @media (prefers-reduced-motion: reduce) {
    animation: none;
  }
`;

export const BrandFooter = styled.div`
  position: absolute;
  bottom: 2.5rem;
  left: 0;
  right: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;

  span {
    font-size: 0.8125rem;
    color: rgba(255, 255, 255, 0.7);
  }
`;

export const EuFlag = styled.div`
  width: 32px;
  height: 22px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.8);
`;

export const FormPanel = styled.div`
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
  background: #f7f8fa;

  @media (max-width: 900px) {
    min-height: 100vh;
    justify-content: flex-start;
    background: linear-gradient(
      180deg,
      rgba(248, 73, 123, 0.03) 0%,
      #f7f8fa 50%
    );
  }
`;

export const MobileAccents = styled.div`
  display: none;

  @media (max-width: 900px) {
    display: block;
    position: absolute;
    inset: 0;
    pointer-events: none;
    background-image:
      linear-gradient(rgba(248, 73, 123, 0.08) 1px, transparent 1px),
      linear-gradient(90deg, rgba(248, 73, 123, 0.08) 1px, transparent 1px);
    background-size: 64px 64px;
    mask-image: radial-gradient(
      ellipse at center,
      rgba(0, 0, 0, 0.6) 0%,
      rgba(0, 0, 0, 0) 70%
    );
    -webkit-mask-image: radial-gradient(
      ellipse at center,
      rgba(0, 0, 0, 0.6) 0%,
      rgba(0, 0, 0, 0) 70%
    );
  }
`;

export const LanguageSelectorWrapper = styled.div`
  position: absolute;
  top: 1.5rem;
  right: 1.5rem;
  z-index: 10;
`;

export const LangSelectorContainer = styled.div`
  position: relative;
`;

export const LangButton = styled.button`
  display: flex;
  align-items: center;
  gap: 0.375rem;
  height: 36px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: #495057;
  background: #f1f3f5;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: var(--font-body);

  &:hover {
    background: #e9ecef;
  }

  svg {
    flex-shrink: 0;
  }
`;

export const LangDropdown = styled.div`
  position: absolute;
  top: calc(100% + 0.25rem);
  right: 0;
  min-width: 100%;
  background: white;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  z-index: 10;
`;

interface LangOptionProps {
  $selected?: boolean;
}

export const LangOption = styled.button<LangOptionProps>`
  display: block;
  width: 100%;
  padding: 0.5rem 0.75rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: ${(props: LangOptionProps) =>
    props.$selected ? APP_COLOR : '#495057'};
  background: ${(props: LangOptionProps) =>
    props.$selected
      ? `color-mix(in srgb, ${APP_COLOR} 8%, transparent)`
      : 'transparent'};
  border: none;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
  font-family: var(--font-body);

  &:hover {
    background: #f1f3f5;
  }
`;

export const MobileHeader = styled.div`
  display: none;

  @media (max-width: 900px) {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1.5rem;

    img {
      height: 1.75rem;
      width: auto;
    }
  }
`;

export const FormContainer = styled.div`
  display: flex;
  flex-direction: column;
  margin: 0 auto;
  width: 100%;
  max-width: 440px;
  padding: 2rem 1.5rem;

  @media (min-width: 901px) {
    padding: 2rem 3rem;
  }

  @media (max-width: 900px) {
    flex: 1;
    justify-content: center;
  }
`;

export const FormHeader = styled.div`
  margin-bottom: 2rem;
  text-align: center;

  .eyebrow {
    font-family: var(--font-heading);
    margin: 0 0 0.75rem;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: #5a6577;
  }

  h2 {
    font-family: var(--font-heading);
    font-size: 1.75rem;
    font-weight: 700;
    color: #333333;
    margin: 0;
  }
`;

export const ProductHighlight = styled.span`
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
`;

export const Divider = styled.div`
  margin: 0 auto 2rem;
  height: 1px;
  width: 14rem;
  background: #e6eaf1;

  @media (max-width: 900px) {
    background: transparent;
  }
`;

export const Actions = styled.div`
  display: flex;
  flex-direction: column;
  gap: 1rem;
`;

export const PrimaryButton = styled.button`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  width: 100%;
  padding: 1rem 1.5rem;
  font-size: 1rem;
  font-weight: 600;
  color: white;
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
  border: none;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 4px 12px rgba(248, 73, 123, 0.3);
  font-family: var(--font-body);

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(248, 73, 123, 0.4);
  }

  svg {
    flex-shrink: 0;
  }
`;

export const SignupPrompt = styled.p`
  text-align: center;
  font-size: 0.875rem;
  color: #41506b;
  margin: 2rem 0 0;

  a {
    color: var(--app-color);
    text-decoration: none;
    font-weight: 500;

    &:hover {
      text-decoration: underline;
    }
  }
`;

export const MobileFooter = styled.div`
  display: none;

  @media (max-width: 900px) {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 1.5rem;
    margin-top: auto;

    span {
      font-size: 0.8125rem;
      color: #adb5bd;
    }
  }
`;

export const MobileEuFlag = styled.div`
  width: 32px;
  height: 22px;
  background: #e9ecef;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #adb5bd;
  opacity: 0.5;
`;
